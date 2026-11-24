"""Tool Invocation Journal 幂等/状态机/Fencing 验证。在 api 容器内运行（真实 PostgreSQL）。"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import select

from agent_runtime.db import RunRecord, SessionLocal, ToolInvocation, append_event
from agent_runtime.recovery.invocations import (
    InvocationConflictError,
    InvocationUnknownError,
    ToolInvocationService,
    canonical_arguments_hash,
    make_tool_idempotency_key,
)
from agent_runtime.rls import set_tenant_context

TENANT = "demo-bank"
USER = "analyst-01"


async def seed_run(session, run_id):
    session.add(RunRecord(run_id=run_id, tenant_id=TENANT, user_id=USER,
                          session_id="ij", agent_id="a", prompt="p", status="RUNNING", metadata_json={}))
    await session.commit()


async def session():
    s = SessionLocal()
    await set_tenant_context(s, TENANT)
    return s


async def main():
    run_id = uuid4()
    s = await session()
    await seed_run(s, run_id)
    await s.close()

    tool_call_id = "call-stable-001"
    key = make_tool_idempotency_key(TENANT, str(run_id), tool_call_id)
    print("key_stable", make_tool_idempotency_key(TENANT, str(run_id), tool_call_id) == key)
    print("key_no_retry", "retry" not in key and len(key) == 64)

    svc = ToolInvocationService()

    # begin -> execute
    s = await session()
    d1 = await svc.begin(s, run_id=run_id, tenant_id=TENANT, tool_call_id=tool_call_id,
                         tool_name="demo.echo", arguments={"text": "hello"},
                         idempotency_key=key, fencing_token=1, lease_owner="w1", idempotent_provider=False)
    print("first_decision", d1["decision"])
    print("canonical_hash_ok", d1["arg_hash"] == canonical_arguments_hash({"text": "hello"}))
    await s.close()

    # confirm with stale fencing (0) -> rejected
    s = await session()
    try:
        await svc.confirm(s, tenant_id=TENANT, idempotency_key=key, fencing_token=0, result={"v": 1})
        print("stale_confirm_rejected", False)
    except InvocationConflictError:
        print("stale_confirm_rejected", True)
        await s.rollback()
    await s.close()

    # confirm with valid fencing
    s = await session()
    await svc.confirm(s, tenant_id=TENANT, idempotency_key=key, fencing_token=2, result={"v": "ok"})
    await s.commit()
    print("confirmed")
    await s.close()

    # diagnostic: check state after valid confirm
    s = await session()
    diag = (await s.execute(select(ToolInvocation.status, ToolInvocation.fencing_token)
                            .where(ToolInvocation.idempotency_key == key))).one_or_none()
    print("state_after_confirm", diag)
    await s.close()

    # reuse: CONFIRMED returns cached result, no re-execution
    s = await session()
    d2 = await svc.begin(s, run_id=run_id, tenant_id=TENANT, tool_call_id=tool_call_id,
                         tool_name="demo.echo", arguments={"text": "hello"},
                         idempotency_key=key, fencing_token=3, lease_owner="w2")
    print("reuse_decision", d2["decision"], "reused_result", d2.get("result"))
    await s.close()

    # same key, different args -> conflict
    s = await session()
    try:
        await svc.begin(s, run_id=run_id, tenant_id=TENANT, tool_call_id=tool_call_id,
                        tool_name="demo.echo", arguments={"text": "DIFFERENT"},
                        idempotency_key=key, fencing_token=4, lease_owner="w3")
        print("arg_conflict_rejected", False)
    except InvocationConflictError:
        print("arg_conflict_rejected", True)
        await s.rollback()
    await s.close()

    # UNKNOWN path: new key, begin DISPATCHED then mark_unknown -> reconciliation required
    call2 = "call-unknown-002"
    key2 = make_tool_idempotency_key(TENANT, str(run_id), call2)
    s = await session()
    await svc.begin(s, run_id=run_id, tenant_id=TENANT, tool_call_id=call2,
                    tool_name="nonidem.tool", arguments={"x": 1},
                    idempotency_key=key2, fencing_token=1, lease_owner="w1", idempotent_provider=False)
    await s.close()
    s = await session()
    await svc.mark_unknown(s, tenant_id=TENANT, idempotency_key=key2, fencing_token=2, last_error="timeout")
    await s.commit()
    await s.close()
    s = await session()
    try:
        await svc.begin(s, run_id=run_id, tenant_id=TENANT, tool_call_id=call2,
                        tool_name="nonidem.tool", arguments={"x": 1},
                        idempotency_key=key2, fencing_token=3, lease_owner="w2")
        print("unknown_no_blind_retry", False)
    except InvocationUnknownError:
        print("unknown_no_blind_retry", True)
        await s.rollback()
    await s.close()

    # reconciliation resolves to CONFIRMED
    s = await session()
    rec = await svc.reconcile(s, tenant_id=TENANT, idempotency_key=key2, fencing_token=4,
                              resolved_status="CONFIRMED", resolved_result={"reconciled": True},
                              actor="auditor-01", note="verified with provider")
    print("reconciled_status", rec["status"])
    await s.commit()
    await s.close()

    # final status
    s = await session()
    row = (await s.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == key2))).scalar_one()
    print("final_status", row.status, "reconciliation_status", row.reconciliation_status)
    await s.close()


asyncio.run(main())
