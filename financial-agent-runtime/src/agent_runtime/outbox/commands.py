from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from temporalio.client import Client

from agent_runtime.db import (
    RunRecord,
    WorkflowOutbox,
    append_event,
)
from agent_runtime.rls import system_session

logger = logging.getLogger(__name__)

WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS = 300

COMMAND_TYPES = ("START_WORKFLOW", "PAUSE_WORKFLOW", "RESUME_WORKFLOW", "APPROVAL_DECISION")
SIGNAL_BY_COMMAND = {
    "PAUSE_WORKFLOW": "pause",
    "RESUME_WORKFLOW": "resume",
    "APPROVAL_DECISION": "provide_approval",
}


async def enqueue_command(
    session: Any,
    *,
    run_id: UUID,
    tenant_id: str,
    command_type: str,
    command_id: str,
    payload: dict[str, Any],
) -> bool:
    """在调用方事务内幂等入队。返回 True=新增，False=已存在（重复请求）。"""
    if command_type not in COMMAND_TYPES:
        raise ValueError(f"unsupported command type: {command_type}")
    existing = (await session.execute(
        select(WorkflowOutbox.outbox_id).where(WorkflowOutbox.command_id == command_id)
    )).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(WorkflowOutbox(
        run_id=run_id,
        tenant_id=tenant_id,
        command_id=command_id,
        command_type=command_type,
        payload=payload,
        status="PENDING",
        next_attempt_at=datetime.now(timezone.utc),
    ))
    return True


async def _mark_dispatched(session: Any, outbox_id: UUID, run_id: UUID, tenant_id: str, command_type: str) -> None:
    row = await session.get(WorkflowOutbox, outbox_id)
    if row is None:
        return
    row.status = "DISPATCHED"
    row.dispatched_at = datetime.now(timezone.utc)
    row.lease_until = None
    row.last_error = None
    await append_event(session, run_id, tenant_id, f"{command_type}_DISPATCHED", {"command_id": row.command_id})


async def _mark_failed(session: Any, outbox_id: UUID, exc: Exception) -> None:
    row = await session.get(WorkflowOutbox, outbox_id)
    if row is None:
        return
    row.lease_until = None
    row.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
    if row.attempts >= 20:
        row.status = "DEAD"
        run = await session.get(RunRecord, row.run_id)
        if run is not None:
            run.status = "FAILED"
            run.error = "signal_command_dead_lettered"
            await append_event(session, run.run_id, row.tenant_id, "OUTBOX_DEAD_LETTERED", {"error": row.last_error})
    else:
        row.status = "PENDING"
        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(60, 2 ** min(row.attempts, 6)))


async def dispatch_commands_once(client: Client, session_factory: Any) -> int:
    """从 Outbox 领取并派发一批命令。至少一次投递；重复 command_id 由 Workflow 去重。"""
    now = datetime.now(timezone.utc)
    claimed: list[dict[str, Any]] = []
    async with system_session(session_factory, "outbox_dispatcher") as session:
        result = await session.execute(
            select(WorkflowOutbox)
            .where(or_(
                (WorkflowOutbox.status == "PENDING") & (WorkflowOutbox.next_attempt_at <= now),
                (WorkflowOutbox.status == "DISPATCHING") & (WorkflowOutbox.lease_until <= now),
            ))
            .order_by(WorkflowOutbox.created_at.asc())
            .limit(20)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars())
        for row in rows:
            row.status = "DISPATCHING"
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=60)
            claimed.append({
                "outbox_id": row.outbox_id, "run_id": row.run_id, "tenant_id": row.tenant_id,
                "command_type": row.command_type, "command_id": row.command_id, "payload": row.payload,
            })
        if claimed:
            await session.commit()

    dispatched = 0
    for item in claimed:
        workflow_id = f"agent-run/{item['tenant_id']}/{item['run_id']}"
        try:
            handle = client.get_workflow_handle(workflow_id)
            if item["command_type"] == "START_WORKFLOW":
                from agent_runtime.contracts import WorkflowInput
                from agent_runtime.workflow import AgentRunWorkflow
                payload = WorkflowInput.model_validate(item["payload"]).model_dump(mode="json")
                try:
                    await client.start_workflow(
                        AgentRunWorkflow.run, payload, id=workflow_id,
                        task_queue=_task_queue(),
                        **workflow_start_kwargs(payload),
                    )
                except Exception as start_exc:
                    # 只有明确"已存在"才视作前一次已启动成功；其它错误（网络抖动、
                    # input 校验失败、task queue 错误）必须原样抛出走重试，不能 SKIPPED。
                    msg = str(start_exc).lower()
                    if "already started" in msg or "already exists" in msg or "workflow already" in msg:
                        await handle.describe()
                    else:
                        raise start_exc
            else:
                signal_name = SIGNAL_BY_COMMAND[item["command_type"]]
                signal_payload = dict(item["payload"] or {})
                signal_payload["command_id"] = item["command_id"]
                await handle.signal(signal_name, signal_payload)
            async with system_session(session_factory, "outbox_dispatcher") as session:
                await _mark_dispatched(
                    session, item["outbox_id"], item["run_id"], item["tenant_id"], item["command_type"],
                )
                await session.commit()
            dispatched += 1
        except Exception as exc:
            async with system_session(session_factory, "outbox_dispatcher") as session:
                message = str(exc).lower()
                if "already completed" in message or "not found" in message or "workflow execution" in message and "completed" in message:
                    # Workflow reached a terminal state before the signal could be delivered.
                    # Do not retry forever; record a terminal skipped state.
                    row = await session.get(WorkflowOutbox, item["outbox_id"])
                    if row is not None:
                        row.status = "SKIPPED"
                        row.last_error = "workflow already completed before signal delivery"
                        row.lease_until = None
                        # 信号无法送达时 workflow 已终结，DB 状态必须同步收口，
                        # 否则 Run 永久停在 PAUSED/WAITING_* 再也无法操作。
                        from agent_runtime.db import RunRecord
                        run_row = await session.get(RunRecord, item["run_id"])
                        if run_row is not None and run_row.status in {"CREATED", "QUEUED", "RUNNING", "PAUSED", "WAITING_INPUT", "WAITING_APPROVAL"}:
                            run_row.status = "CANCELLED"
                            run_row.updated_at = datetime.now(timezone.utc)
                        await append_event(session, item["run_id"], item["tenant_id"],
                                           f"{item['command_type']}_SKIPPED", {"command_id": item["command_id"]})
                    logger.warning("outbox command skipped (workflow completed): %s", item["command_id"])
                else:
                    await _mark_failed(session, item["outbox_id"], exc)
                await session.commit()
    return dispatched


def workflow_start_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """Temporal start parameters for a run. execution_timeout bounds a lost
    worker's workflow so it cannot remain running forever."""
    return {
        "execution_timeout": timedelta(
            seconds=int(payload["max_duration_seconds"]) + WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS
        )
    }


def _task_queue() -> str:
    from agent_runtime.config import settings
    return settings.temporal_task_queue
