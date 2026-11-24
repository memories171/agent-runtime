"""FI orchestration helper (runs inside api container, has temporalio + DB access)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from uuid import UUID

from agent_runtime.config import settings

TASK_QUEUE = "agent-runtime-fi"
STATE_DIR = Path("/app/fi-data/run-state")
STATE_DIR.mkdir(parents=True, exist_ok=True)
SIGNAL_DIR = Path("/app/fi-data/signals")


def wid(run_id: str) -> str:
    return f"fi/{run_id}"


async def client() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def start_slow(duration: int = 120, notify_pct: int = 40, item_count: int = 60):
    run_id = str(uuid4())
    c = await client()
    params = {
        "run_id": run_id, "tenant_id": "fi-tenant", "duration_seconds": duration,
        "heartbeat_interval_seconds": 2, "notify_at_pct": notify_pct, "item_count": item_count,
    }
    handle = await c.start_workflow("FISlowWorkflow", params, id=wid(run_id), task_queue=TASK_QUEUE,
                                    execution_timeout=timedelta(minutes=20))
    (STATE_DIR / "slow.json").write_text(json.dumps({
        "run_id": run_id, "workflow_id": handle.id,
        "started_at": time.time(), "duration": duration,
        "workflow_run_id": handle.result_run_id if hasattr(handle, "result_run_id") else None,
    }))
    print(json.dumps({"action": "start_slow", "run_id": run_id, "workflow_id": handle.id}))


async def check_slow():
    state = json.loads((STATE_DIR / "slow.json").read_text())
    run_id = state["run_id"]
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    desc = await handle.describe()
    result = None
    try:
        result = await asyncio.wait_for(handle.result(), timeout=5)
    except Exception:
        pass
    signal_files = list(SIGNAL_DIR.glob(f"{run_id}.reached_target_pct.json"))
    signal_payload = json.loads(signal_files[0].read_text()) if signal_files else None
    activity_failures = 0
    activity_completions = 0
    async for ev in handle.fetch_history_events():
        if ev.HasField("activity_task_failed_event_attributes"):
            activity_failures += 1
        if ev.HasField("activity_task_completed_event_attributes"):
            activity_completions += 1
    print(json.dumps({
        "action": "check_slow", "run_id": run_id, "workflow_status": str(desc.status),
        "result": result, "signal_at_40pct": bool(signal_payload), "signal_payload": signal_payload,
        "activity_failed_count": activity_failures, "activity_completed_count": activity_completions,
        "workflow_id": wid(run_id),
    }, ensure_ascii=False, default=str))


async def start_effect(operation: str = "fixture.write", delay: int = 0):
    run_id = str(uuid4())
    key = f"fi-effect-{uuid4().hex}"
    c = await client()
    params = {
        "run_id": run_id, "tenant_id": "fi-tenant", "idempotency_key": key,
        "operation": operation, "delay_after_effect_seconds": delay,
    }
    handle = await c.start_workflow("FIEffectWorkflow", params, id=wid(run_id), task_queue=TASK_QUEUE,
                                    execution_timeout=timedelta(minutes=10))
    (STATE_DIR / "effect.json").write_text(json.dumps({"run_id": run_id, "idempotency_key": key,
                                                       "workflow_id": handle.id}))
    print(json.dumps({"action": "start_effect", "run_id": run_id, "idempotency_key": key}))


async def check_effect():
    state = json.loads((STATE_DIR / "effect.json").read_text())
    c = await client()
    handle = c.get_workflow_handle(wid(state["run_id"]))
    desc = await handle.describe()
    result = None
    try:
        result = await asyncio.wait_for(handle.result(), timeout=5)
    except Exception:
        pass
    print(json.dumps({"action": "check_effect", "run_id": state["run_id"], "idempotency_key": state["idempotency_key"],
                      "workflow_status": str(desc.status), "result": result}, ensure_ascii=False, default=str))


async def start_unknown():
    run_id = str(uuid4())
    key = f"fi-unknown-{uuid4().hex}"
    c = await client()
    params = {"run_id": run_id, "tenant_id": "fi-tenant", "idempotency_key": key}
    handle = await c.start_workflow("FIUnknownWorkflow", params, id=wid(run_id), task_queue=TASK_QUEUE,
                                    execution_timeout=timedelta(minutes=5))
    (STATE_DIR / "unknown.json").write_text(json.dumps({"run_id": run_id, "idempotency_key": key}))
    print(json.dumps({"action": "start_unknown", "run_id": run_id, "idempotency_key": key}))


async def check_unknown():
    state = json.loads((STATE_DIR / "unknown.json").read_text())
    c = await client()
    handle = c.get_workflow_handle(wid(state["run_id"]))
    try:
        result = await asyncio.wait_for(handle.result(), timeout=5)
    except Exception as exc:
        result = {"error": str(exc)}
    print(json.dumps({"action": "check_unknown", "run_id": state["run_id"], "result": result}, default=str))


async def describe_workflow(run_id: str):
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    desc = await handle.describe()
    print(json.dumps({"action": "describe", "run_id": run_id, "workflow_id": wid(run_id),
                      "status": str(desc.status)}, default=str))


async def signal_run(run_id: str, signal: str):
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    await handle.signal(signal, {"command_id": f"fi-{signal}-{run_id}"})
    state = await handle.query("get_state")
    print(json.dumps({"action": "signal", "signal": signal, "run_id": run_id, "state": state}))


async def check_pause_state(run_id: str):
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    state = await handle.query("get_state")
    desc = await handle.describe()
    print(json.dumps({"action": "pause_state", "run_id": run_id, "state": state,
                      "status": str(desc.status)}, default=str))


async def start_approval():
    from agent_runtime.db import RunRecord, SessionLocal
    from agent_runtime.rls import set_tenant_context

    run_id = str(uuid4())
    tenant = "fi-tenant"
    tool_call_id = f"fi-approval-{uuid4().hex}"
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        s.add(RunRecord(run_id=UUID(run_id), tenant_id=tenant, user_id="fi-requester",
                        session_id="fi-approval", agent_id="fi", prompt="approval fi",
                        status="WAITING_APPROVAL", version=0, pause_state="NONE", metadata_json={}))
        await s.commit()
    c = await client()
    params = {"run_id": run_id, "tenant_id": tenant, "tool_call_id": tool_call_id}
    handle = await c.start_workflow("FIApprovalWorkflow", params, id=wid(run_id), task_queue=TASK_QUEUE,
                                    execution_timeout=timedelta(minutes=10))
    (STATE_DIR / "approval.json").write_text(json.dumps({
        "run_id": run_id, "workflow_id": handle.id, "tool_call_id": tool_call_id, "tenant": tenant,
    }))
    print(json.dumps({"action": "start_approval", "run_id": run_id, "workflow_id": handle.id,
                      "tool_call_id": tool_call_id}))


async def approval_state():
    from agent_runtime.db import ApprovalBinding, SessionLocal
    from agent_runtime.rls import set_tenant_context
    from sqlalchemy import select

    state = json.loads((STATE_DIR / "approval.json").read_text())
    run_id, tenant = state["run_id"], state["tenant"]
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    wf_state = await handle.query("get_state")
    desc = await handle.describe()
    db_status = None
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        row = (await s.execute(select(ApprovalBinding.approval_status)
                               .where(ApprovalBinding.run_id == UUID(run_id)))).scalar_one_or_none()
        db_status = row
    print(json.dumps({"action": "approval_state", "run_id": run_id, "workflow_state": wf_state,
                      "workflow_status": str(desc.status), "db_approval_status": db_status}, default=str))


async def decide_approval(approved: bool = True):
    from agent_runtime.approval.service import ApprovalService
    from agent_runtime.db import SessionLocal
    from agent_runtime.rls import set_tenant_context

    state = json.loads((STATE_DIR / "approval.json").read_text())
    run_id, tenant = state["run_id"], state["tenant"]
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    wf_state = await handle.query("get_state")
    approval_id = wf_state["approval_id"]
    approver = "fi-approver"
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        await ApprovalService().decide(s, tenant_id=tenant, approval_id=approval_id,
                                       approved=approved, approver_id=approver)
        await s.commit()
    # Signal with a stable command_id so we can also verify duplicate de-dup.
    cmd = f"fi-approval-decision-{run_id}"
    await handle.signal("provide_approval", {"command_id": cmd, "approved": approved,
                                             "approver_user_id": approver})
    print(json.dumps({"action": "decide_approval", "run_id": run_id, "approval_id": approval_id,
                      "approved": approved, "signal_command_id": cmd}))


async def check_approval():
    state = json.loads((STATE_DIR / "approval.json").read_text())
    run_id = state["run_id"]
    c = await client()
    handle = c.get_workflow_handle(wid(run_id))
    try:
        result = await asyncio.wait_for(handle.result(), timeout=10)
    except Exception as exc:
        result = {"error": str(exc)}
    print(json.dumps({"action": "check_approval", "run_id": run_id, "result": result}, default=str))


async def test5_dispatcher_crash():
    """真实 WorkflowOutbox Dispatcher 崩溃窗口：Signal 已投递但未标记 DISPATCHED。

    Simulated by rewinding a DISPATCHED row back to an expired DISPATCHING lease,
    then dispatching again. The SAME command_id must be de-duplicated by the
    workflow so the approval is consumed exactly once (a second consume would
    raise InvalidApprovalError and fail the workflow).
    """
    from agent_runtime.db import RunRecord, SessionLocal, WorkflowOutbox, ApprovalBinding
    from agent_runtime.rls import set_tenant_context
    from agent_runtime.approval.service import ApprovalService
    from agent_runtime.outbox.commands import enqueue_command, dispatch_commands_once
    from sqlalchemy import select, update

    tenant = "demo-bank"
    run_id = str(uuid4())
    tool_call_id = f"fi-disp-{uuid4().hex}"
    cmd_id = f"approval-decision:fi-disp:{run_id}"
    wf_id = f"agent-run/{tenant}/{run_id}"

    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        s.add(RunRecord(run_id=UUID(run_id), tenant_id=tenant, user_id="fi-requester",
                        session_id="fi-disp", agent_id="fi", prompt="dispatcher fi",
                        status="WAITING_APPROVAL", version=0, pause_state="NONE", metadata_json={}))
        await s.commit()

    c = await client()
    params = {"run_id": run_id, "tenant_id": tenant, "tool_call_id": tool_call_id}
    handle = await c.start_workflow("FIApprovalWorkflow", params, id=wf_id,
                                    task_queue=TASK_QUEUE, execution_timeout=timedelta(minutes=10))
    # Wait for create_approval_binding_activity to persist the binding.
    for _ in range(30):
        st = await handle.query("get_state")
        if st.get("approval_id"):
            break
        await asyncio.sleep(0.2)
    approval_id = (await handle.query("get_state"))["approval_id"]

    # API would decide the approval first (DB -> APPROVED), then enqueue signal command.
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        await ApprovalService().decide(s, tenant_id=tenant, approval_id=approval_id,
                                       approved=True, approver_id="fi-approver")
        await s.commit()
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        added = await enqueue_command(s, run_id=UUID(run_id), tenant_id=tenant,
                                      command_type="APPROVAL_DECISION", command_id=cmd_id,
                                      payload={"approved": True, "approver_user_id": "fi-approver"})
        await s.commit()

    # 1st dispatch: signal delivered + row marked DISPATCHED.
    n1 = await dispatch_commands_once(c, SessionLocal)
    await asyncio.sleep(1)
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        row = (await s.execute(select(WorkflowOutbox.status).where(WorkflowOutbox.command_id == cmd_id))).scalar_one()
    status_after_first = row

    # Simulate crash window: signal delivered but DISPATCHED commit lost -> expired lease.
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        await s.execute(update(WorkflowOutbox).where(WorkflowOutbox.command_id == cmd_id).values(
            status="DISPATCHING", lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await s.commit()

    # 2nd dispatch: SAME command_id re-delivered after "crash".
    n2 = await dispatch_commands_once(c, SessionLocal)
    result = await asyncio.wait_for(handle.result(), timeout=15)
    final_state = await handle.query("get_state")
    async with SessionLocal() as s:
        await set_tenant_context(s, tenant)
        row2 = (await s.execute(select(WorkflowOutbox.status, WorkflowOutbox.attempts)
                                .where(WorkflowOutbox.command_id == cmd_id))).one()
        db_status = (await s.execute(select(ApprovalBinding.approval_status)
                                     .where(ApprovalBinding.run_id == UUID(run_id)))).scalar_one()
    print(json.dumps({
        "action": "test5_dispatcher_crash", "run_id": run_id, "command_id": cmd_id,
        "enqueued_new": added, "first_dispatch_count": n1, "status_after_first": status_after_first,
        "second_dispatch_count": n2, "final_outbox_status": row2[0], "outbox_attempts": row2[1],
        "workflow_state": final_state, "workflow_result": result, "db_approval_status": db_status,
        "duplicate_signal_deduped": final_state.get("processed_commands_count") == 1,
        "consumed_exactly_once": result.get("status") == "CONSUMED" and db_status == "CONSUMED",
    }, ensure_ascii=False, default=str))


async def test_pause_resume(duration: int = 30):
    """Full Pause/Resume state-machine check under a slow activity.

    Verifies:
      - pause/resume signals accepted while a non-cancellable activity runs;
      - command_id de-duplication (duplicate signals do not grow processed set);
      - safe-boundary pause after the activity completes (workflow stays RUNNING);
      - resume clears flags and the SAME workflow id completes with attempt=1.
    """
    run_id = str(uuid4())
    c = await client()
    params = {
        "run_id": run_id, "tenant_id": "fi-tenant", "duration_seconds": duration,
        "heartbeat_interval_seconds": 2, "notify_at_pct": 40, "item_count": 60,
    }
    handle = await c.start_workflow(
        "FISlowWorkflow", params, id=wid(run_id), task_queue=TASK_QUEUE,
        execution_timeout=timedelta(minutes=10),
    )
    t0 = time.time()
    # Concurrent pause signals: A twice (dedupe) + B once = 2 unique commands.
    await handle.signal("pause", {"command_id": "fi-pause-A"})
    await handle.signal("pause", {"command_id": "fi-pause-A"})
    await handle.signal("pause", {"command_id": "fi-pause-B"})

    # Mid-activity: activity must keep running (not interrupted by pause signal).
    await asyncio.sleep(max(6, int(duration * 0.2)))
    early_state = await handle.query("get_state")
    early_status = str((await handle.describe()).status)
    pct40 = bool(list(SIGNAL_DIR.glob(f"{run_id}.reached_target_pct.json")))

    # After activity finishes: safe-boundary pause keeps workflow RUNNING, paused.
    await asyncio.sleep(max(2, duration - max(6, int(duration * 0.2))) + 4)
    mid_state = await handle.query("get_state")
    mid_status = str((await handle.describe()).status)

    # Resume with duplicates: R twice (dedupe) + S once = 2 more unique commands.
    await handle.signal("resume", {"command_id": "fi-resume-R"})
    await handle.signal("resume", {"command_id": "fi-resume-R"})
    await handle.signal("resume", {"command_id": "fi-resume-S"})

    result = await asyncio.wait_for(handle.result(), timeout=60)
    final_state = await handle.query("get_state")
    elapsed = round(time.time() - t0, 1)
    print(json.dumps({
        "action": "test_pause_resume", "run_id": run_id, "workflow_id": wid(run_id),
        "duration_seconds": duration, "elapsed_seconds": elapsed,
        "early_state": early_state, "early_status": early_status, "activity_reached_40pct": pct40,
        "mid_state": mid_state, "mid_status": mid_status,
        "final_state": final_state, "result": result,
        "unique_commands_expected": 4,
        "dedupe_ok": final_state.get("processed_commands_count") == 4,
        "paused_at_boundary": mid_status == "1" and mid_state.get("pause_requested") is True,
        "flags_cleared": final_state.get("pause_requested") is False and final_state.get("resume_requested") is False,
        "same_workflow_id": result.get("workflow_id") == wid(run_id),
    }, ensure_ascii=False, default=str))


async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "start-slow":
        await start_slow(int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif cmd == "check-slow":
        await check_slow()
    elif cmd == "start-effect":
        await start_effect(
            sys.argv[2] if len(sys.argv) > 2 else "fixture.write",
            int(sys.argv[3]) if len(sys.argv) > 3 else 0,
        )
    elif cmd == "check-effect":
        await check_effect()
    elif cmd == "start-unknown":
        await start_unknown()
    elif cmd == "check-unknown":
        await check_unknown()
    elif cmd == "describe":
        await describe_workflow(sys.argv[2])
    elif cmd == "pause":
        await signal_run(sys.argv[2], "pause")
    elif cmd == "resume":
        await signal_run(sys.argv[2], "resume")
    elif cmd == "pause-state":
        await check_pause_state(sys.argv[2])
    elif cmd == "test3":
        await test_pause_resume(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif cmd == "start-approval":
        await start_approval()
    elif cmd == "approval-state":
        await approval_state()
    elif cmd == "decide-approval":
        await decide_approval(sys.argv[2].lower() != "reject" if len(sys.argv) > 2 else True)
    elif cmd == "check-approval":
        await check_approval()
    elif cmd == "test5":
        await test5_dispatcher_crash()
    else:
        print("commands: start-slow [sec] | check-slow | start-effect | check-effect | start-unknown | check-unknown | describe <run_id> | pause <run_id> | resume <run_id> | pause-state <run_id> | test3 [sec] | start-approval | approval-state | decide-approval [approve|reject] | check-approval | test5")


asyncio.run(main())
