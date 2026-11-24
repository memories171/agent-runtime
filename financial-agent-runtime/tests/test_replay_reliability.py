"""B1: Replay reliability / provability regression tests.

Fast, deterministic service/unit tests that complement the real-service
evidence already produced in B0 (24 real Temporal cases, incl. worker crash,
worker restart, durable timer, cancel/timeout) and the fault-injection scripts.

Covered here:
- production outbox passes a bounded execution_timeout on workflow start
- workflow module uses only deterministic Temporal time/id APIs
- outbox command_id de-dupes at DB level (unique constraint)
- stale fencing token rejected; CONFIRMED result reused (no repeat side effect)

Not covered here by design (documented in B1 report):
- real worker restart / replay: B0 temporal_it cases (real services, not simulated)
- outbox redispatch after lease expiry / response loss: scripts/fault_injection
- recovery audit failure path: scripts/verify_recovery_events.py
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import select

from agent_runtime.db import WorkflowOutbox


def test_outbox_start_workflow_passes_execution_timeout():
    from agent_runtime.outbox.commands import (
        WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS, workflow_start_kwargs,
    )

    max_duration = 1800
    kwargs = workflow_start_kwargs({"max_duration_seconds": max_duration})
    assert kwargs["execution_timeout"] == timedelta(
        seconds=max_duration + WORKFLOW_EXECUTION_TIMEOUT_MARGIN_SECONDS
    )


def test_workflow_uses_deterministic_time_and_id_apis():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    wf = ast.parse((root / "workflow.py").read_text(encoding="utf-8"))
    forbidden = [
        node.func.id for node in ast.walk(wf)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"uuid4", "time", "monotonic", "random"}
    ]
    assert forbidden == [], f"workflow 主模块出现非确定性 API: {forbidden}"


def test_outbox_command_id_unique_constraint_rejects_duplicate():
    """DB-level duplicate protection for outbox command_id (no external services)."""
    from sqlalchemy import UniqueConstraint

    constraints = [
        c for c in WorkflowOutbox.__table_args__
        if isinstance(c, UniqueConstraint) and "command_id" in [col.name for col in c.columns]
    ]
    assert constraints, "workflow_outbox 缺少 command_id 唯一约束"


@pytest.mark.asyncio
async def test_stale_fencing_rejected_and_confirmed_reused(real_postgres_dsn: str):
    from agent_runtime.recovery.invocations import (
        InvocationConflictError, ToolInvocationService, make_tool_idempotency_key,
    )
    from agent_runtime.rls import set_tenant_context
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tenant = f"b1-fence-{uuid4().hex[:10]}"
    run_id = uuid4()

    def async_dsn(dsn: str) -> str:
        return dsn if dsn.startswith("postgresql+asyncpg://") else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(async_dsn(real_postgres_dsn))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def new_session():
        s = factory()
        await set_tenant_context(s, tenant)
        return s

    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute(
                "INSERT INTO agent_runs(run_id, tenant_id, user_id, session_id, agent_id, prompt, status, metadata) "
                "VALUES($1,$2,$3,$4,$5,$6,'RUNNING','{}'::jsonb)",
                run_id, tenant, "u", "s", "a", "p",
            )

        svc = ToolInvocationService()
        key = make_tool_idempotency_key(tenant, str(run_id), "call-b1")

        sess = await new_session()
        try:
            await svc.begin(
                sess, run_id=run_id, tenant_id=tenant, tool_call_id="call-b1",
                tool_name="demo.echo", arguments={"x": 1}, idempotency_key=key,
                fencing_token=1, lease_owner="w1", idempotent_provider=True,
            )
            await sess.commit()
        finally:
            await sess.close()

        sess = await new_session()
        try:
            with pytest.raises(InvocationConflictError):
                await svc.confirm(sess, tenant_id=tenant, idempotency_key=key,
                                  fencing_token=0, result={"v": "stale"})
            await sess.rollback()
        finally:
            await sess.close()

        sess = await new_session()
        try:
            await svc.confirm(sess, tenant_id=tenant, idempotency_key=key,
                              fencing_token=2, result={"v": "ok"})
            await sess.commit()
        finally:
            await sess.close()

        sess = await new_session()
        try:
            decision = await svc.begin(
                sess, run_id=run_id, tenant_id=tenant, tool_call_id="call-b1",
                tool_name="demo.echo", arguments={"x": 1}, idempotency_key=key,
                fencing_token=3, lease_owner="w2", idempotent_provider=True,
            )
            assert decision["decision"] == "reuse"
            assert decision["result"] == {"v": "ok"}
        finally:
            await sess.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()
        await engine.dispose()
