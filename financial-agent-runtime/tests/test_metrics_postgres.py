"""Phase A metrics real PostgreSQL tests.

Requires TEST_DATABASE_URL pointing at the same non-superuser application
database used by the runtime. Tests are skipped when it is not set.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from agent_runtime.contracts import Principal
from agent_runtime.monitoring.metrics_service import (
    REASON_ROOT_MISMATCH,
    ExecutionMetricsService,
    TreeIntegrityPrecheck,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=120)
NOW = START + timedelta(seconds=60)


def _principal(tenant: str, user: str, *, tenant_scope: bool = True) -> Principal:
    scopes = {"agent:execute", "runs:read:tenant"} if tenant_scope else {"agent:execute"}
    return Principal(
        tenant_id=tenant, user_id=user, scopes=scopes, permissions=set(scopes),
        authentication_method="dev",
    )


async def _seed_run(conn, tenant: str, run_id, user: str, kind: str = "SUB_AGENT") -> None:
    await conn.execute(
        """INSERT INTO agent_runs
           (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
           VALUES ($1::uuid, $2, $3, 'metrics-session', 'agent', 'metrics test', 'RUNNING', $4, '{}'::json)
           ON CONFLICT (run_id) DO NOTHING""",
        run_id, tenant, user, kind,
    )


async def _link(
    conn,
    *,
    tenant: str,
    parent,
    child,
    root,
    node_id: str,
    attempt: int = 1,
    status: str = "RUNNING",
    started_offset: float | None = 0,
    completed_offset: float | None = None,
    key: str | None = None,
) -> None:
    token = uuid4()
    started = START + timedelta(seconds=started_offset) if started_offset is not None else None
    completed = START + timedelta(seconds=completed_offset) if completed_offset is not None else None
    await conn.execute(
        """INSERT INTO capability_tokens
           (token_id, tenant_id, run_id, parent_token_id, subject_run_id, subject_agent_id,
            capabilities, resource_scope, depth, max_depth, expires_at, signature_hash)
           VALUES ($1::uuid, $2, $3::uuid, NULL, $4::uuid, 'executor',
                   '["agent:execute"]'::jsonb, '{}'::jsonb, 1, 8, now(), $5)
           ON CONFLICT (tenant_id, token_id) DO NOTHING""",
        token, tenant, parent, child, f"sig-{token}",
    )
    await conn.execute(
        """INSERT INTO agent_delegations
           (delegation_id, tenant_id, parent_run_id, child_run_id, root_run_id,
            parent_agent_id, child_agent_id, capability_token_id, delegation_key,
            status, node_id, depth, max_depth, attempt, started_at, completed_at)
           VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                   'parent', 'executor', $6::uuid, $7, $8, $9, 1, 8, $10, $11, $12)
           ON CONFLICT (tenant_id, delegation_key) DO NOTHING""",
        uuid4(), tenant, parent, child, root, token,
        key or f"dlg-{uuid4()}", status, node_id, attempt, started, completed,
    )


async def _collect(tenant: str, root, user: str, *, tenant_scope: bool = True):
    return await ExecutionMetricsService.collect(
        tenant_id=tenant,
        root_run_id=root,
        principal=_principal(tenant, user, tenant_scope=tenant_scope),
        start_time=START,
        end_time=END,
        now=NOW,
    )


def _new_tenant() -> str:
    return f"metrics-{uuid4().hex[:24]}"


@pytest.mark.asyncio
async def test_parallel_attempts_and_partial_intervals(real_postgres_dsn: str):
    tenant, user = _new_tenant(), "metrics-owner"
    root, c1, c2, c3, c4 = (uuid4() for _ in range(5))
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, user, "ROOT")
            for run in (c1, c2, c3, c4):
                await _seed_run(conn, tenant, run, user)
            await _link(conn, tenant=tenant, parent=root, child=c1, root=root,
                        node_id="a", status="RUNNING", started_offset=10)
            await _link(conn, tenant=tenant, parent=root, child=c2, root=root,
                        node_id="b", status="COMPLETED", started_offset=5, completed_offset=20)
            await _link(conn, tenant=tenant, parent=root, child=c3, root=root,
                        node_id="c", status="FAILED", started_offset=15, completed_offset=25)
            await _link(conn, tenant=tenant, parent=root, child=c4, root=root,
                        node_id="c", attempt=2, status="COMPLETED",
                        started_offset=30, completed_offset=35)

        result = await _collect(tenant, root, user)
        assert result.data_quality == "OK"
        assert result.current_parallelism == 1
        assert result.max_parallelism == 3
        assert result.total_agent_active_time == pytest.approx(80.0)
        assert result.average_parallelism == pytest.approx(80.0 / 120.0)
        assert result.delegation_attempt_count == 4
        assert result.distinct_child_run_count == 4
        assert result.distinct_node_count == 3
        assert result.partial_interval_count == 1
        assert result.unknown_interval_count == 0
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_child_acl_exclusion_does_not_leak(real_postgres_dsn: str):
    tenant, owner, other = _new_tenant(), "metrics-owner", "metrics-other"
    root, visible, hidden, hidden_grandchild = (uuid4() for _ in range(4))
    hidden_id = str(hidden)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, owner, "ROOT")
            await _seed_run(conn, tenant, visible, owner)
            await _seed_run(conn, tenant, hidden, other)
            await _seed_run(conn, tenant, hidden_grandchild, other)
            await _link(conn, tenant=tenant, parent=root, child=visible, root=root,
                        node_id="visible", status="COMPLETED",
                        started_offset=1, completed_offset=10)
            await _link(conn, tenant=tenant, parent=root, child=hidden, root=root,
                        node_id="hidden", status="COMPLETED",
                        started_offset=1, completed_offset=100)
            await _link(conn, tenant=tenant, parent=hidden, child=hidden_grandchild, root=root,
                        node_id="hidden-child", status="COMPLETED",
                        started_offset=1, completed_offset=100)

        result = await _collect(tenant, root, owner, tenant_scope=False)
        assert result.data_quality == "DEGRADED"
        assert "HIDDEN_RUNS_EXCLUDED" in result.unavailable_reasons
        assert result.delegation_attempt_count == 1
        assert result.total_agent_active_time == pytest.approx(9.0)
        payload = result.model_dump_json()
        assert hidden_id not in payload
        assert "metrics-other" not in payload
        assert "child_run_id" not in payload
        assert "delegation_id" not in payload
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_root_mismatch_is_detected_without_dropping_descendant(real_postgres_dsn: str):
    tenant, user = _new_tenant(), "metrics-owner"
    root, child, grandchild, wrong_root = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for run, kind in [(root, "ROOT"), (child, "SUB_AGENT"), (grandchild, "SUB_AGENT")]:
                await _seed_run(conn, tenant, run, user, kind)
            await _link(conn, tenant=tenant, parent=root, child=child, root=root,
                        node_id="child", status="COMPLETED",
                        started_offset=1, completed_offset=10)
            await _link(conn, tenant=tenant, parent=child, child=grandchild, root=wrong_root,
                        node_id="grandchild", status="COMPLETED",
                        started_offset=2, completed_offset=12)

        result = await _collect(tenant, root, user)
        assert result.data_quality == "DEGRADED"
        assert REASON_ROOT_MISMATCH in result.unavailable_reasons
        assert result.delegation_attempt_count == 2
        assert result.max_parallelism == 2
        assert result.total_agent_active_time == pytest.approx(19.0)

        scope = await ExecutionMetricsService._load_scope(
            tenant_id=tenant, root_run_id=root,
            principal=_principal(tenant, user),
        )
        findings, _ = TreeIntegrityPrecheck.analyze(
            scope.all_records, scope.scope_records, root, scope.missing_run_ids,
        )
        mismatch = next(f for f in findings if f.reason == REASON_ROOT_MISMATCH)
        assert str(grandchild) in mismatch.child_run_ids
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_cycle_and_duplicate_child_are_excluded(real_postgres_dsn: str):
    tenant, user = _new_tenant(), "metrics-owner"
    root, a, b, dup_parent = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for run, kind in [(root, "ROOT"), (a, "SUB_AGENT"), (b, "SUB_AGENT"), (dup_parent, "SUB_AGENT")]:
                await _seed_run(conn, tenant, run, user, kind)
            await _link(conn, tenant=tenant, parent=root, child=a, root=root,
                        node_id="a", status="COMPLETED", started_offset=1, completed_offset=5)
            await _link(conn, tenant=tenant, parent=a, child=b, root=root,
                        node_id="b", status="COMPLETED", started_offset=2, completed_offset=6)
            await _link(conn, tenant=tenant, parent=b, child=a, root=root,
                        node_id="cycle", status="COMPLETED", started_offset=3, completed_offset=7)
            await _link(conn, tenant=tenant, parent=dup_parent, child=b, root=root,
                        node_id="duplicate", status="COMPLETED", started_offset=4, completed_offset=8)

        result = await _collect(tenant, root, user)
        assert result.data_quality == "UNAVAILABLE"
        assert result.max_parallelism == 0
        assert result.delegation_attempt_count == 0
        assert "CYCLE_DETECTED" in result.unavailable_reasons
        assert "DUPLICATE_CHILD_LINK" in result.unavailable_reasons
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_tenant_isolation(real_postgres_dsn: str):
    tenant_a, tenant_b, user = _new_tenant(), _new_tenant(), "metrics-owner"
    root_a, child_a, root_b, child_b = (uuid4() for _ in range(4))
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant_a, root_a, user, "ROOT")
            await _seed_run(conn, tenant_a, child_a, user)
            await _seed_run(conn, tenant_b, root_b, user, "ROOT")
            await _seed_run(conn, tenant_b, child_b, user)
            await _link(conn, tenant=tenant_a, parent=root_a, child=child_a, root=root_a,
                        node_id="a", status="COMPLETED", started_offset=1, completed_offset=5)
            await _link(conn, tenant=tenant_b, parent=root_b, child=child_b, root=root_b,
                        node_id="b", status="COMPLETED", started_offset=1, completed_offset=100)

        result = await _collect(tenant_a, root_a, user)
        assert result.data_quality == "OK"
        assert result.delegation_attempt_count == 1
        assert result.total_agent_active_time == pytest.approx(4.0)
    finally:
        await _cleanup(conn, tenant_a)
        await _cleanup(conn, tenant_b)
        await conn.close()


@pytest.mark.asyncio
async def test_interval_anomalies(real_postgres_dsn: str):
    tenant, user = _new_tenant(), "metrics-owner"
    root, active, terminal, bad_order = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, user, "ROOT")
            for run in (active, terminal, bad_order):
                await _seed_run(conn, tenant, run, user)
            await _link(conn, tenant=tenant, parent=root, child=active, root=root,
                        node_id="active", status="RUNNING", started_offset=None)
            await _link(conn, tenant=tenant, parent=root, child=terminal, root=root,
                        node_id="terminal", status="COMPLETED",
                        started_offset=1, completed_offset=None)
            await _link(conn, tenant=tenant, parent=root, child=bad_order, root=root,
                        node_id="bad-order", status="COMPLETED",
                        started_offset=20, completed_offset=10)

        result = await _collect(tenant, root, user)
        assert result.data_quality == "UNAVAILABLE"
        assert result.unknown_interval_count == 3
        assert "UNKNOWN_INTERVALS" in result.unavailable_reasons
        assert "NO_VALID_INTERVALS" in result.unavailable_reasons
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [34, 40])
async def test_deep_chains(real_postgres_dsn: str, depth: int):
    tenant, user = _new_tenant(), "metrics-owner"
    runs = [uuid4() for _ in range(depth + 1)]
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for index, run in enumerate(runs):
                await _seed_run(conn, tenant, run, user, "ROOT" if index == 0 else "SUB_AGENT")
            for index in range(depth):
                await _link(conn, tenant=tenant, parent=runs[index], child=runs[index + 1],
                            root=runs[0], node_id=f"level-{index}", status="COMPLETED",
                            started_offset=index, completed_offset=index + 10,
                            key=f"deep-{depth}-{index}-{uuid4()}")
        result = await _collect(tenant, runs[0], user)
        assert result.data_quality == "OK"
        assert result.delegation_attempt_count == depth
        assert result.max_parallelism == 10
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [10, 100, 1000, 10000])
async def test_scale_measurements(real_postgres_dsn: str, count: int):
    tenant, user = _new_tenant(), "metrics-owner"
    root = uuid4()
    children = [uuid4() for _ in range(count)]
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, user, "ROOT")
            for child in children:
                await _seed_run(conn, tenant, child, user)
            for index, child in enumerate(children):
                await _link(conn, tenant=tenant, parent=root, child=child, root=root,
                            node_id=f"scale-{index}", status="RUNNING",
                            started_offset=0,
                            key=f"scale-{count}-{index}-{uuid4()}")

        started = time.perf_counter()
        result = await _collect(tenant, root, user)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert result.delegation_attempt_count == count
        assert result.max_parallelism == count
        assert result.data_quality == "OK"
        print(f"SCALE_RESULT count={count} elapsed_ms={elapsed_ms:.2f}")
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_wrong_root_with_no_edges_not_reported_as_ok_zero(real_postgres_dsn: str):
    """P1-1: a wrong root (no delegation edges) must not return all-zero OK."""
    tenant, user = _new_tenant(), "metrics-owner"
    real_root, wrong_root, child = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, real_root, user, "ROOT")
            await _seed_run(conn, tenant, wrong_root, user, "ROOT")
            await _seed_run(conn, tenant, child, user)
            await _link(conn, tenant=tenant, parent=real_root, child=child, root=real_root,
                        node_id="a", status="COMPLETED", started_offset=1, completed_offset=5)

        result = await _collect(tenant, wrong_root, user)
        assert result.data_quality == "MISSING"
        assert "ROOT_UNRESOLVED" in result.unavailable_reasons
        # Real tree still reports OK with the correct root.
        good = await _collect(tenant, real_root, user)
        assert good.data_quality == "OK"
        assert good.delegation_attempt_count == 1
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_leaf_child_as_root_not_reported_as_ok(real_postgres_dsn: str):
    """P1-1: requesting a leaf child as root must not reuse its parent's tree."""
    tenant, user = _new_tenant(), "metrics-owner"
    root, child = uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, user, "ROOT")
            await _seed_run(conn, tenant, child, user)
            await _link(conn, tenant=tenant, parent=root, child=child, root=root,
                        node_id="a", status="COMPLETED", started_offset=1, completed_offset=9)

        # BFS would reach root from child, but child is not the tree root.
        leaf_view = await _collect(tenant, child, user)
        assert leaf_view.data_quality == "MISSING"
        assert "ROOT_UNRESOLVED" in leaf_view.unavailable_reasons

        # The real root still resolves normally.
        root_view = await _collect(tenant, root, user)
        assert root_view.data_quality == "OK"
        assert root_view.delegation_attempt_count == 1
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_data_quality_states_are_distinguished(real_postgres_dsn: str):
    """P1-1: real-zero, denied and missing states are not conflated with OK."""
    tenant, owner, other = _new_tenant(), "metrics-owner", "metrics-other"
    root, hidden_child, visible_child = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, tenant, root, owner, "ROOT")
            await _seed_run(conn, tenant, visible_child, owner)
            await _seed_run(conn, tenant, hidden_child, other)
            await _link(conn, tenant=tenant, parent=root, child=visible_child, root=root,
                        node_id="v", status="COMPLETED", started_offset=1, completed_offset=3)
            await _link(conn, tenant=tenant, parent=root, child=hidden_child, root=root,
                        node_id="h", status="COMPLETED", started_offset=1, completed_offset=3)

        # Owner without tenant-wide read: hidden child is excluded -> degraded.
        owner_view = await _collect(tenant, root, owner, tenant_scope=False)
        assert owner_view.data_quality in {"DEGRADED", "PARTIAL", "DENIED"}
        assert str(hidden_child) not in str(owner_view.model_dump())

        # Tenant-wide reader sees everything and is OK.
        reader_view = await _collect(tenant, root, owner, tenant_scope=True)
        assert reader_view.data_quality == "OK"
        assert reader_view.delegation_attempt_count == 2
    finally:
        await _cleanup(conn, tenant)
        await conn.close()


async def _cleanup(conn, tenant: str) -> None:
    await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
    await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", tenant)
    await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", tenant)
    await conn.execute("DELETE FROM agent_runs WHERE tenant_id = $1", tenant)
