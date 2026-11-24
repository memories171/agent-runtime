"""Agent Tree (3.3.6 Step 2) real PostgreSQL tests.

Covers multi-level trees, duplicate registration, parent termination with late
child completion, orphan/cycle/duplicate detection, root pushdown and strict
per-Run ACL filtering. Requires TEST_DATABASE_URL against a non-superuser
account (RLS enforced); tests are skipped when it is not set.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from agent_runtime.contracts import Principal
from agent_runtime.orchestration.delegation_service import (
    DelegationService,
    deterministic_child_run_id,
)
from agent_runtime.orchestration.models import CapabilityGrant
from agent_runtime.orchestration.token_service import CapabilityTokenService

TENANT_A, TENANT_B = "agent-tree-a", "agent-tree-b"
USER_OWNER, USER_OTHER = "tree-owner", "tree-other"


def _plain_dsn(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


MIGRATION_DSN = _plain_dsn(
    os.getenv("TEST_MIGRATION_DSN")
    or os.getenv("DATABASE_MIGRATION_URL")
    or "postgresql://agent:agent@localhost:5433/agent_runtime"
)
MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "0016_repair_agent_tree_root_backfill.py"
)


def _principal(tenant: str, user: str, *, tenant_scope: bool = False) -> Principal:
    scopes = {"agent:execute", "runs:read:tenant"} if tenant_scope else {"agent:execute"}
    return Principal(
        tenant_id=tenant, user_id=user, scopes=scopes, permissions=set(scopes),
        authentication_method="dev",
    )


async def _seed_run(conn, run_id, tenant, user, kind="ROOT", status="RUNNING", parent=None):
    metadata = {"parent_run_id": str(parent)} if parent else {}
    await conn.execute(
        """INSERT INTO agent_runs
           (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
           VALUES ($1::uuid, $2, $3, 'tree-session', 'agent', 'tree test', $4, $5, $6::json)
           ON CONFLICT (run_id) DO NOTHING""",
        run_id, tenant, user, status, kind, __import__("json").dumps(metadata),
    )


async def _link(conn, *, delegation, tenant, parent, child, root, node_id, agent="executor",
                depth=1, status="RUNNING", attempt=1, key=None):
    token = uuid4()
    await conn.execute(
        """INSERT INTO capability_tokens
           (token_id, tenant_id, run_id, parent_token_id, subject_run_id, subject_agent_id,
            capabilities, resource_scope, depth, max_depth, expires_at, signature_hash)
           VALUES ($1::uuid, $2, $3::uuid, NULL, $4::uuid, $5,
                   '["agent:execute"]'::jsonb, '{}'::jsonb, $6, 3, now() + interval '1 hour', $7)
           ON CONFLICT (tenant_id, token_id) DO NOTHING""",
        token, tenant, parent, child, agent, depth, f"sig-{delegation}",
    )
    await conn.execute(
        """INSERT INTO agent_delegations
           (delegation_id, tenant_id, parent_run_id, child_run_id, root_run_id,
            parent_agent_id, child_agent_id, capability_token_id, delegation_key,
            status, node_id, depth, max_depth, attempt, started_at, completed_at)
           VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                   'parent', $6, $7::uuid, $8, $9, $10, $11, 3, $12, now(), NULL)
           ON CONFLICT (tenant_id, delegation_key) DO NOTHING""",
        delegation, tenant, parent, child, root, agent, token,
        key or f"dlg-{delegation}", status, node_id, depth, attempt,
    )
    return token


async def _cleanup(conn, runs, tenant):
    await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
    await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", tenant)
    await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", tenant)
    await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", runs)


def _root_repair_statements() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("migration_0016", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ROOT_REPAIR_STATEMENTS


async def _seed_chain(conn, tenant: str, root, depth: int, *, wrong_root=None) -> list:
    """Seed root -> ... -> child with an intentionally stale root_run_id."""
    runs = [root] + [uuid4() for _ in range(depth)]
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        for index, run_id in enumerate(runs):
            await _seed_run(conn, run_id, tenant, USER_OWNER,
                            "ROOT" if index == 0 else "SUB_AGENT")
        for index in range(depth):
            await _link(
                conn, delegation=uuid4(), tenant=tenant,
                parent=runs[index], child=runs[index + 1],
                root=wrong_root or root, node_id=f"chain-{depth}-{index}",
                depth=index + 1, key=f"chain-{depth}-{index}-{uuid4()}",
            )
    return runs


@pytest.mark.asyncio
async def test_multi_level_tree_and_acl(real_postgres_dsn: str):
    """parent -> child -> grandchild; strict per-Run ACL hides unauthorized nodes."""
    root, child, grandchild = uuid4(), uuid4(), uuid4()
    child_other = uuid4()
    d1, d2, d3 = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for rid, user, kind in [
                (root, USER_OWNER, "ROOT"),
                (child, USER_OWNER, "SUB_AGENT"),
                (grandchild, USER_OWNER, "SUB_AGENT"),
                (child_other, USER_OTHER, "SUB_AGENT"),
            ]:
                await _seed_run(conn, rid, TENANT_A, user, kind)
            await _link(conn, delegation=d1, tenant=TENANT_A, parent=root, child=child,
                        root=root, node_id="child", depth=1)
            await _link(conn, delegation=d2, tenant=TENANT_A, parent=child, child=grandchild,
                        root=root, node_id="grandchild", depth=2)
            await _link(conn, delegation=d3, tenant=TENANT_A, parent=root, child=child_other,
                        root=root, node_id="child-other", depth=1, agent="analyst")

        owner = _principal(TENANT_A, USER_OWNER)
        view = await DelegationService.get_agent_tree_view(TENANT_A, root, owner)
        ids = {node.execution_id for node in view.nodes}
        assert view.root_visible is True
        assert child in ids and grandchild in ids
        assert child_other not in ids
        root_node = next(n for n in view.nodes if n.execution_id == child)
        assert root_node.has_hidden_children is False

        # A user with tenant-wide read sees all nodes.
        scope_view = await DelegationService.get_agent_tree_view(
            TENANT_A, root, _principal(TENANT_A, USER_OTHER, tenant_scope=True)
        )
        assert {n.execution_id for n in scope_view.nodes} >= {child, grandchild, child_other}

        # Wrong tenant principal cannot see the root at all.
        cross = await DelegationService.get_agent_tree_view(TENANT_B, root, _principal(TENANT_B, USER_OWNER))
        assert cross.root_visible is False
        assert cross.nodes == []
    finally:
        await _cleanup(conn, [root, child, grandchild, child_other], TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_depth_cap_hides_descendants_without_count(real_postgres_dsn: str):
    root, child, grandchild = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, root, TENANT_A, USER_OWNER)
            await _seed_run(conn, child, TENANT_A, USER_OWNER, "SUB_AGENT")
            await _seed_run(conn, grandchild, TENANT_A, USER_OWNER, "SUB_AGENT")
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root, child=child,
                        root=root, node_id="c", depth=1)
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=child, child=grandchild,
                        root=root, node_id="g", depth=2)
        view = await DelegationService.get_agent_tree_view(
            TENANT_A, root, _principal(TENANT_A, USER_OWNER), max_depth=1
        )
        assert {n.execution_id for n in view.nodes} == {child}
        child_node = view.nodes[0]
        assert child_node.has_hidden_children is True
        assert not any(hasattr(child_node, field) and "count" in field
                       for field in child_node.model_dump())
    finally:
        await _cleanup(conn, [root, child, grandchild], TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_duplicate_and_cycle_detection_with_fk_intact(real_postgres_dsn: str):
    """FK-intact anomalies: duplicate child links and a two-node cycle are distinct."""
    root, child, other_parent, cycle_a, cycle_b = (uuid4() for _ in range(5))
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for rid, kind in [
                (root, "ROOT"), (child, "SUB_AGENT"), (other_parent, "ROOT"),
                (cycle_a, "SUB_AGENT"), (cycle_b, "SUB_AGENT"),
            ]:
                await _seed_run(conn, rid, TENANT_A, USER_OWNER, kind)
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root, child=child,
                        root=root, node_id="normal", depth=1)
            # Duplicate relationship: same child has two different existing parents.
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=other_parent,
                        child=child, root=root, node_id="duplicate", depth=1)
            # Two-node cycle: both parent runs exist, so no FK is violated.
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=cycle_a,
                        child=cycle_b, root=root, node_id="cycle-a", depth=1)
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=cycle_b,
                        child=cycle_a, root=root, node_id="cycle-b", depth=2)
        anomalies = await DelegationService.detect_tree_anomalies(
            TENANT_A, root, _principal(TENANT_A, USER_OWNER)
        )
        assert anomalies.orphans == []
        assert child in anomalies.duplicate_links
        assert any({cycle_a, cycle_b} <= set(cycle) for cycle in anomalies.cycles)
        assert TENANT_B not in str(anomalies.model_dump())
    finally:
        runs = [root, child, other_parent, cycle_a, cycle_b]
        await _cleanup(conn, runs, TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_root_mismatch_is_not_reported_as_physical_orphan(real_postgres_dsn: str):
    """A stale root_run_id with an existing parent is not a physical orphan."""
    root, child, wrong_root = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, root, TENANT_A, USER_OWNER)
            await _seed_run(conn, child, TENANT_A, USER_OWNER, "SUB_AGENT")
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root, child=child,
                        root=wrong_root, node_id="root-mismatch", depth=1)
        anomalies = await DelegationService.detect_tree_anomalies(
            TENANT_A, root, _principal(TENANT_A, USER_OWNER)
        )
        assert child not in anomalies.orphans
        assert anomalies.duplicate_links == []
        assert anomalies.cycles == []
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            stored_root = await conn.fetchval(
                "SELECT root_run_id FROM agent_delegations WHERE tenant_id=$1 AND child_run_id=$2::uuid",
                TENANT_A, child,
            )
        assert stored_root == wrong_root
    finally:
        await _cleanup(conn, [root, child], TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_physical_orphan_defensive_detection_with_fk_bypassed():
    """Defensive-only: physical orphan needs an isolated FK-bypassed fixture.

    This test uses a dedicated tenant and a migration-superuser connection with
    session_replication_role toggled only for the insert. Production constraints
    are unchanged and all rows are removed in finally.
    """
    tenant = f"agent-tree-orphan-{uuid4().hex[:12]}"
    root, child, normal_child, missing_parent = uuid4(), uuid4(), uuid4(), uuid4()
    orphan_child = uuid4()
    conn = await asyncpg.connect(MIGRATION_DSN)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("SET LOCAL session_replication_role = 'replica'")
            for rid, kind in [
                (root, "ROOT"), (child, "SUB_AGENT"),
                (normal_child, "SUB_AGENT"), (orphan_child, "SUB_AGENT"),
            ]:
                await _seed_run(conn, rid, tenant, USER_OWNER, kind)
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=root,
                        child=child, root=root, node_id="normal", depth=1)
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=root,
                        child=normal_child, root=root, node_id="normal-2", depth=1)
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=missing_parent,
                        child=orphan_child, root=root, node_id="physical-orphan", depth=1)
            await conn.execute("SET LOCAL session_replication_role = 'origin'")

        anomalies = await DelegationService.detect_tree_anomalies(
            tenant, root, _principal(tenant, USER_OWNER)
        )
        assert orphan_child in anomalies.orphans
        assert child not in anomalies.orphans
        assert normal_child not in anomalies.orphans
    finally:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", tenant)
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])",
                               [root, child, normal_child, orphan_child])
        await conn.close()


@pytest.mark.asyncio
async def test_create_delegation_persists_root_run_id(real_postgres_dsn: str):
    """New delegations persist root_run_id via ROOT parent resolution."""
    parent = uuid4()
    child = deterministic_child_run_id(TENANT_A, str(parent), "c", 1)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, parent, TENANT_A, USER_OWNER)
            await _seed_run(conn, child, TENANT_A, USER_OWNER, "SUB_AGENT")
        principal = _principal(TENANT_A, USER_OWNER)
        root_token = await CapabilityTokenService.issue_root(principal, parent, ttl_seconds=3600)
        record, created = await DelegationService.create_delegation(
            tenant_id=TENANT_A, parent_run_id=parent, parent_token_id=root_token.token_id,
            requested=CapabilityGrant(capabilities={"agent:execute"}),
            parent_agent_id="parent", child_agent_id="executor", node_id="c",
            attempt=1, actor=principal,
        )
        assert created is True
        assert record.root_run_id == parent
        persisted = await DelegationService.get_delegation(TENANT_A, record.delegation_id)
        assert persisted.root_run_id == parent
    finally:
        await _cleanup(conn, [parent, child], TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_migration_repair_traces_true_root_at_34_and_40_levels():
    """0016 must find the real root beyond 0015's depth-32 proxy."""
    tenant = "agent-tree-mig-deep"
    roots = {34: uuid4(), 40: uuid4()}
    conn = await asyncpg.connect(MIGRATION_DSN)
    all_runs: list = []
    expected: dict = {}
    try:
        for depth, root in roots.items():
            runs = await _seed_chain(conn, tenant, root, depth, wrong_root=uuid4())
            all_runs.extend(runs)
            for index in range(depth):
                expected[runs[index]] = root

        async with conn.transaction():
            stored = await conn.fetch(
                "SELECT parent_run_id, root_run_id FROM agent_delegations WHERE tenant_id=$1",
                tenant,
            )
            assert len(stored) == 74
            assert all(row["root_run_id"] != expected[row["parent_run_id"]] for row in stored)

        transaction = conn.transaction()
        await transaction.start()
        try:
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for statement in _root_repair_statements():
                await conn.execute(statement)
            repaired = await conn.fetch(
                "SELECT parent_run_id, root_run_id FROM agent_delegations WHERE tenant_id=$1",
                tenant,
            )
            assert len(repaired) == 74
            assert all(row["root_run_id"] == expected[row["parent_run_id"]] for row in repaired)
            anomalies = await conn.fetchval(
                "SELECT count(*) FROM agent_delegation_root_backfill_anomalies WHERE tenant_id=$1",
                tenant,
            )
            assert anomalies == 0
        finally:
            await transaction.rollback()
    finally:
        await _cleanup(conn, all_runs, tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_migration_repair_reports_cycles_ambiguous_and_cross_tenant_links():
    """Unresolvable chains stay NULL and are reported with a concrete reason."""
    tenant = "agent-tree-mig-anomaly"
    foreign_tenant = "agent-tree-mig-foreign"
    root, p1, p2, child = (uuid4() for _ in range(4))
    c2a, c2b = uuid4(), uuid4()
    c3a, c3b, c3c = uuid4(), uuid4(), uuid4()
    cross_child, foreign_parent = uuid4(), uuid4()
    amb_parent, amb_child = uuid4(), uuid4()
    local_runs = [root, p1, p2, child, c2a, c2b, c3a, c3b, c3c, cross_child,
                  amb_parent, amb_child]
    conn = await asyncpg.connect(MIGRATION_DSN)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("SET LOCAL session_replication_role = 'replica'")
            for run_id in local_runs:
                await _seed_run(conn, run_id, tenant, USER_OWNER,
                                "ROOT" if run_id == root else "SUB_AGENT")
            await _seed_run(conn, foreign_parent, foreign_tenant, USER_OWNER, "ROOT")
            wrong = uuid4()
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=c2a, child=c2b,
                        root=wrong, node_id="c2-a")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=c2b, child=c2a,
                        root=wrong, node_id="c2-b")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=c3a, child=c3b,
                        root=wrong, node_id="c3-a")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=c3b, child=c3c,
                        root=wrong, node_id="c3-b")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=c3c, child=c3a,
                        root=wrong, node_id="c3-c")
            # Duplicate child relationship: each edge still has its own clear root.
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=p1, child=child,
                        root=wrong, node_id="dup-1")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=p2, child=child,
                        root=wrong, node_id="dup-2")
            # Truly ambiguous root: amb_parent itself has two parent links.
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=p1, child=amb_parent,
                        root=wrong, node_id="amb-1")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=p2, child=amb_parent,
                        root=wrong, node_id="amb-2")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=amb_parent,
                        child=amb_child, root=wrong, node_id="amb-child")
            await _link(conn, delegation=uuid4(), tenant=tenant, parent=foreign_parent,
                        child=cross_child, root=wrong, node_id="cross-tenant")
            await conn.execute("SET LOCAL session_replication_role = 'origin'")

        transaction = conn.transaction()
        await transaction.start()
        try:
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            for statement in _root_repair_statements():
                await conn.execute(statement)
            null_roots = await conn.fetchval(
                "SELECT count(*) FROM agent_delegations WHERE tenant_id=$1 AND root_run_id IS NULL",
                tenant,
            )
            assert null_roots == 7
            duplicate_roots = await conn.fetch(
                "SELECT root_run_id FROM agent_delegations WHERE tenant_id=$1 AND child_run_id=$2::uuid",
                tenant, child,
            )
            assert {row["root_run_id"] for row in duplicate_roots} == {p1, p2}
            reasons = await conn.fetch(
                "SELECT reason, count(*)::int AS n FROM agent_delegation_root_backfill_anomalies "
                "WHERE tenant_id=$1 GROUP BY reason",
                tenant,
            )
            by_reason = {row["reason"]: row["n"] for row in reasons}
            assert by_reason.get("cycle", 0) == 5
            assert by_reason.get("ambiguous_parent", 0) == 1
            assert by_reason.get("cross_tenant_run", 0) == 1
        finally:
            await transaction.rollback()
    finally:
        await _cleanup(conn, local_runs, tenant)
        await _cleanup(conn, [foreign_parent], foreign_tenant)
        await conn.close()


@pytest.mark.asyncio
async def test_anomalies_hide_cross_user_child_uuid(real_postgres_dsn: str):
    """P1-2: a root reader must not see a hidden child's UUID in any anomaly."""
    root, hidden_child, visible_child = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, root, TENANT_A, USER_OWNER)
            await _seed_run(conn, visible_child, TENANT_A, USER_OWNER, "SUB_AGENT")
            await _seed_run(conn, hidden_child, TENANT_A, USER_OTHER, "SUB_AGENT")
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root,
                        child=visible_child, root=root, node_id="visible", depth=1)
            # hidden_child has two hidden parents -> would be a duplicate link if
            # ACL were not applied; its UUID must never appear.
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root,
                        child=hidden_child, root=root, node_id="hidden-1", depth=1)
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=visible_child,
                        child=hidden_child, root=root, node_id="hidden-2", depth=2)

        owner = _principal(TENANT_A, USER_OWNER)  # no tenant-wide read
        anomalies = await DelegationService.detect_tree_anomalies(TENANT_A, root, owner)
        payload = str(anomalies.model_dump())
        assert str(hidden_child) not in payload
        assert hidden_child not in anomalies.duplicate_links
        assert hidden_child not in anomalies.orphans
        assert all(hidden_child not in cycle for cycle in anomalies.cycles)

        # A tenant-wide reader is explicitly allowed to see all children.
        reader = _principal(TENANT_A, USER_OWNER, tenant_scope=True)
        scoped = await DelegationService.detect_tree_anomalies(TENANT_A, root, reader)
        assert hidden_child in scoped.duplicate_links
    finally:
        await _cleanup(conn, [root, hidden_child, visible_child], TENANT_A)
        await conn.close()


@pytest.mark.asyncio
async def test_anomalies_reject_cross_tenant_principal(real_postgres_dsn: str):
    """P1-2: tenant_id must come from the authenticated principal."""
    root, child = uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, root, TENANT_A, USER_OWNER)
            await _seed_run(conn, child, TENANT_A, USER_OWNER, "SUB_AGENT")
            await _link(conn, delegation=uuid4(), tenant=TENANT_A, parent=root,
                        child=child, root=root, node_id="t", depth=1)
        with pytest.raises(ValueError, match="authenticated principal"):
            await DelegationService.detect_tree_anomalies(
                TENANT_A, root, _principal(TENANT_B, USER_OWNER)
            )
    finally:
        await _cleanup(conn, [root, child], TENANT_A)
        await conn.close()
