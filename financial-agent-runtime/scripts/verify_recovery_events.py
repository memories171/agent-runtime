"""验证 Worker 重启扫描记录的恢复事件与对账状态。在 api 容器内运行。"""
from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import func, select

from agent_runtime.db import RunEvent, RunRecord, SessionLocal, ToolInvocation, WorkerFailureRecord
from agent_runtime.rls import set_tenant_context


async def main():
    async with SessionLocal() as session:
        await set_tenant_context(session, "demo-bank")
        failures = (await session.execute(
            select(WorkerFailureRecord).where(WorkerFailureRecord.worker_id.like("worker-boot%"))
            .order_by(WorkerFailureRecord.fencing_token.desc())
        )).scalars().all()
        boot_runs = [str(f.run_id) for f in failures]
        print("recovery_records", len(failures))
        print("sample_run", boot_runs[0] if boot_runs else None)
        if failures:
            latest = failures[0]
            print("recovery_status", latest.recovery_status)
            print("recovery_started_at", bool(latest.recovery_started_at))
            print("recovery_completed_at", bool(latest.recovery_completed_at))
            print("fencing_token", latest.fencing_token)
            event_counts = (await session.execute(
                select(RunEvent.event_type, func.count()).where(RunEvent.run_id == latest.run_id)
                .group_by(RunEvent.event_type)
            )).all()
            events = {row[0]: row[1] for row in event_counts}
            print("WORKER_FAILED", events.get("WORKER_FAILED", 0))
            print("TEMPORAL_HISTORY_REPLAY", events.get("TEMPORAL_HISTORY_REPLAY", 0))
            print("WORKER_RECONNECTED", events.get("WORKER_RECONNECTED", 0))
            print("WORKFLOW_RESUMED", events.get("WORKFLOW_RESUMED", 0))
            db_run = await session.get(RunRecord, latest.run_id)
            print("run_still_exists", db_run is not None)

        # 幂等状态机分布
        status_counts = (await session.execute(
            select(ToolInvocation.status, func.count()).group_by(ToolInvocation.status)
        )).all()
        print("invocation_statuses", {r[0]: r[1] for r in status_counts})


asyncio.run(main())
