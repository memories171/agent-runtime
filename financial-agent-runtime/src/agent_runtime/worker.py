import asyncio
import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from temporalio.client import Client
from temporalio.worker import Worker

from agent_runtime.activities import (
    agent_decide_activity,
    append_history_event_activity,
    authorize_tool_activity,
    execute_tool_activity,
    mark_worker_failure_recovered_activity,
    persist_checkpoint_activity,
    persist_status_activity,
    persist_telemetry_activity,
    persist_worker_failure_activity,
    record_checkpoint_restore_activity,
    record_worker_recovery_activity,
    retrieve_memory_activity,
    load_skills_activity,
    create_approval_binding_activity,
    validate_approval_binding_activity,
    consume_approval_binding_activity,
)
from agent_runtime.activities_context import (
    assemble_context_activity,
    summarize_context_activity,
    load_context_state_activity,
)
from agent_runtime.config import settings
from agent_runtime.orchestration.activities import (
    build_child_context_activity,
    create_delegation_activity,
    mark_delegation_cancel_requested_activity,
    mark_delegation_started_activity,
    mark_delegation_terminal_activity,
    settle_delegation_activity,
)
from agent_runtime.graph.activities import (
    append_graph_event_activity,
    load_graph_activity,
    load_latest_graph_for_run_activity,
    persist_graph_activity,
    plan_graph_activity,
    replan_graph_activity,
    save_node_result_activity,
    update_graph_status_activity,
    update_node_status_activity,
    validate_graph_proposal_activity,
)
from agent_runtime.db import RunRecord, WorkflowOutbox, SessionLocal, append_event
from agent_runtime.contracts import WorkflowInput
from agent_runtime.rls import system_session
from agent_runtime.workflow import AgentRunWorkflow
from agent_runtime.tools.registry import configure_mcp_servers


_RECONNECT_DEDUP_WINDOW_SECONDS = 300


async def record_worker_reconnect(boot_id: str) -> int:
    """Worker 启动时扫描其接管的运行中任务，记录 Temporal 驱动的恢复事件。

    不创建/重启 Workflow，也不从 Checkpoint 手动执行 step；仅记录业务恢复审计。
    fencing token 使用启动时间戳的 epoch 秒，保证后续 worker 的写入覆盖旧值。

    多副本 Worker 并发启动时，用 Run 行锁 + 时间窗幂等，确保同一 Run 在窗口内
    只写一次恢复事件，避免每个副本都制造一套虚假的 WORKER_FAILED 审计。
    """
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from agent_runtime.db import RunEvent, WorkerFailureRecord, append_event

    now = datetime.now(timezone.utc)
    fencing_token = int(now.timestamp())
    recovered = 0
    async with system_session(SessionLocal, "outbox_dispatcher") as session:
        rows = (await session.execute(
            select(RunRecord).where(
                RunRecord.status.in_(["RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "PAUSED"]),
                RunRecord.deleted_at.is_(None),
            ).with_for_update()
        )).scalars()
        for run in list(rows):
            recent = (await session.execute(
                select(RunEvent.event_id).where(
                    RunEvent.run_id == run.run_id,
                    RunEvent.event_type == "WORKER_RECONNECTED",
                    RunEvent.created_at >= now - timedelta(seconds=_RECONNECT_DEDUP_WINDOW_SECONDS),
                ).limit(1)
            )).scalar_one_or_none()
            if recent is not None:
                continue
            idem = f"{run.run_id}:worker-reconnect:{boot_id}"
            session.add(WorkerFailureRecord(
                failure_id=uuid4(),
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                worker_id=f"worker-boot/{boot_id}",
                reason="worker process restart; Temporal resumes workflow from history",
                recovery_status="RECOVERED",
                recovery_count=1,
                retry_count=0,
                recovery_position="temporal-history-replay",
                idempotency_key=idem,
                duplicate_protection=True,
                recovery_started_at=now,
                recovery_completed_at=now,
                recovery_attempt=1,
                fencing_token=fencing_token,
            ))
            for event_type in ("WORKER_FAILED", "TEMPORAL_HISTORY_REPLAY", "WORKER_RECONNECTED", "WORKFLOW_RESUMED"):
                await append_event(session, run.run_id, run.tenant_id, event_type, {
                    "boot_id": boot_id, "fencing_token": fencing_token,
                    "note": "Temporal auto-redispatch; no new workflow created",
                })
            recovered += 1
        if recovered:
            await session.commit()
    return recovered


async def dispatch_outbox_once(client: Client) -> int:
    """Claim and dispatch pending workflow commands using the transactional outbox."""
    from agent_runtime.outbox.commands import dispatch_commands_once
    return await dispatch_commands_once(client, SessionLocal)


async def outbox_dispatch_loop(client: Client) -> None:
    while True:
        try:
            await dispatch_outbox_once(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 单次派发失败（DB 抖动/网络异常）不能让整个 worker 退出。
            logging.exception("outbox dispatch failed; retrying in 2s")
        await asyncio.sleep(2)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings.validate_runtime_config()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)

    async def _startup_side_tasks() -> None:
        # 外部 MCP 发现与历史 Run 恢复扫描都可能很慢（网络超时/海量记录），
        # 不能阻塞 outbox dispatch 与 workflow polling，否则新任务一直卡在 QUEUED。
        try:
            await configure_mcp_servers(settings.mcp_servers_json)
        except Exception:
            logging.exception("MCP server discovery failed at startup; worker continues without MCP tools")
        boot_id = str(uuid4())
        try:
            reconnected = await record_worker_reconnect(boot_id)
            if reconnected:
                logging.info("worker reconnect: recorded recovery for %d running run(s)", reconnected)
        except Exception:
            logging.exception("worker reconnect scan failed (non-fatal)")

    startup_task = asyncio.create_task(_startup_side_tasks())
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[AgentRunWorkflow],
        activities=[
            persist_status_activity,
            agent_decide_activity,
            authorize_tool_activity,
            execute_tool_activity,
            append_history_event_activity,
            retrieve_memory_activity,
            load_skills_activity,
            persist_telemetry_activity,
            persist_checkpoint_activity,
            persist_worker_failure_activity,
            mark_worker_failure_recovered_activity,
            record_worker_recovery_activity,
            record_checkpoint_restore_activity,
            create_approval_binding_activity,
            validate_approval_binding_activity,
            consume_approval_binding_activity,
            assemble_context_activity,
            summarize_context_activity,
            load_context_state_activity,
            plan_graph_activity,
            validate_graph_proposal_activity,
            persist_graph_activity,
            load_graph_activity,
            load_latest_graph_for_run_activity,
            append_graph_event_activity,
            save_node_result_activity,
            update_node_status_activity,
            update_graph_status_activity,
            replan_graph_activity,
            build_child_context_activity,
            create_delegation_activity,
            mark_delegation_started_activity,
            mark_delegation_terminal_activity,
            mark_delegation_cancel_requested_activity,
            settle_delegation_activity,
        ],
        max_concurrent_activities=50,
        max_concurrent_workflow_tasks=100,
    )
    try:
        await asyncio.gather(worker.run(), outbox_dispatch_loop(client))
    finally:
        startup_task.cancel()
        try:
            await startup_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
