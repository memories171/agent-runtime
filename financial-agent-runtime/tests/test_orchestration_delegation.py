"""DelegationService 的真实 PostgreSQL 测试与 sub_agent Graph 校验。"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import inspect, select

from agent_runtime.contracts import Principal
from agent_runtime.db import AgentDelegationRecord as DelegationRow, session_for_tenant
from agent_runtime.graph.models import GraphLimits, GraphProposal
from agent_runtime.graph.validate import validate_proposal
from agent_runtime.orchestration.delegation_service import (
    DelegationService,
    _row_to_record,
    deterministic_child_run_id,
    deterministic_delegation_key,
)
from agent_runtime.orchestration.models import CapabilityGrant
from agent_runtime.orchestration.token_service import CapabilityDenied, CapabilityTokenService
from agent_runtime.rls import set_tenant_context

TENANT_A, TENANT_B = "delegation-a", "delegation-b"


def _principal(tenant: str) -> Principal:
    return Principal(
        tenant_id=tenant, user_id="delegation-user", scopes=["agent:execute", "external:send"],
        permissions=["agent:execute", "external:send"], authentication_method="dev",
    )


async def _seed_run(conn, run_id, tenant, kind="ROOT", status="RUNNING"):
    await conn.execute(
        """INSERT INTO agent_runs
           (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
           VALUES ($1::uuid, $2, 'delegation-user', 'delegation-session', 'agent', 'delegation test',
                   $3, $4, '{}'::json)
           ON CONFLICT (run_id) DO NOTHING""",
        run_id, tenant, status, kind,
    )


@pytest.mark.asyncio
async def test_delegation_lifecycle_and_idempotency(real_postgres_dsn: str):
    parent = uuid4()
    child = deterministic_child_run_id(TENANT_A, str(parent), "child-a", 1)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, parent, TENANT_A)
            await _seed_run(conn, child, TENANT_A, "SUB_AGENT")
    finally:
        await conn.close()

    principal = _principal(TENANT_A)
    root = await CapabilityTokenService.issue_root(principal, parent, ttl_seconds=3600)
    request = CapabilityGrant(capabilities={"agent:execute"}, timeout_seconds=600)
    try:
        first, created = await DelegationService.create_delegation(
            tenant_id=TENANT_A, parent_run_id=parent, parent_token_id=root.token_id,
            requested=request, parent_agent_id="parent", child_agent_id="executor",
            node_id="child-a", attempt=1, graph_id=None, actor=principal,
        )
        second, created_again = await DelegationService.create_delegation(
            tenant_id=TENANT_A, parent_run_id=parent, parent_token_id=root.token_id,
            requested=request, parent_agent_id="parent", child_agent_id="executor",
            node_id="child-a", attempt=1, graph_id=None, actor=principal,
        )
        assert created is True and created_again is False
        assert first.delegation_id == second.delegation_id
        assert first.delegation_key == deterministic_delegation_key(TENANT_A, str(parent), "child-a", 1)
        assert first.child_workflow_id == f"agent-run/{TENANT_A}/{child}"
        assert first.status == "ISSUED"
        assert first.updated_at is not None

        started = await DelegationService.mark_started(TENANT_A, first.delegation_id)
        assert started.updated_at is not None
        await DelegationService.mark_waiting(TENANT_A, first.delegation_id)
        await DelegationService.mark_started(TENANT_A, first.delegation_id)
        terminal = await DelegationService.mark_terminal(
            TENANT_A, first.delegation_id, "COMPLETED",
            result_summary={"child_run_id": str(child), "status": "SUCCEEDED"},
        )
        assert terminal.status == "COMPLETED"
        assert terminal.result_summary["status"] == "SUCCEEDED"
        assert terminal.updated_at is not None

        tree = await DelegationService.get_agent_tree(TENANT_A, parent)
        assert [item.delegation_id for item in tree] == [first.delegation_id]

        with pytest.raises(RuntimeError):
            await DelegationService.mark_started(TENANT_A, first.delegation_id)
    finally:
        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [parent, child])
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_delegation_updated_at_materialized_after_commit(real_postgres_dsn: str):
    """commit 后服务端生成的 updated_at 必须先物化，再同步转换为 record。"""
    parent = uuid4()
    child = deterministic_child_run_id(TENANT_A, str(parent), "updated-at", 1)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, parent, TENANT_A)
            await _seed_run(conn, child, TENANT_A, "SUB_AGENT")
    finally:
        await conn.close()

    principal = _principal(TENANT_A)
    root = await CapabilityTokenService.issue_root(principal, parent, ttl_seconds=3600)
    try:
        record, created = await DelegationService.create_delegation(
            tenant_id=TENANT_A, parent_run_id=parent, parent_token_id=root.token_id,
            requested=CapabilityGrant(capabilities={"agent:execute"}),
            parent_agent_id="parent", child_agent_id="executor", node_id="updated-at",
            attempt=1, actor=principal,
        )
        assert created is True
        assert record.updated_at is not None
        persisted = await DelegationService.get_delegation(TENANT_A, record.delegation_id)
        assert persisted.updated_at == record.updated_at

        running = await DelegationService.mark_started(TENANT_A, record.delegation_id)
        assert running.updated_at is not None
        assert running.updated_at >= persisted.updated_at

        settled, won = await DelegationService.settle_terminal(
            TENANT_A, record.delegation_id, "COMPLETED",
            result_summary={"status": "SUCCEEDED"},
        )
        assert won is True
        assert settled.updated_at is not None
        reread = await DelegationService.get_delegation(TENANT_A, record.delegation_id)
        assert reread.updated_at == settled.updated_at

        # AsyncSession 的显式证明：带 onupdate 的 UPDATE commit 后，该列进入
        # expired 状态；直接同步读取会触发 MissingGreenlet，刷新后则安全。
        async with session_for_tenant(TENANT_A) as session:
            row = (await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == TENANT_A,
                    DelegationRow.delegation_id == record.delegation_id,
                )
            )).scalar_one()
            assert "updated_at" not in inspect(row).expired_attributes
            row.error = "expiry_probe"
            await session.commit()
            assert "updated_at" in inspect(row).expired_attributes
            # session_for_tenant 的 set_config(..., true) 随 commit 结束而失效，
            # 显式重建租户上下文后再刷新，模拟生产请求中的同一事务语义。
            await set_tenant_context(session, TENANT_A)
            await session.refresh(row, attribute_names=["updated_at"])
            assert "updated_at" not in inspect(row).expired_attributes
            assert _row_to_record(row).updated_at is not None
    finally:
        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [parent, child])
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_delegation_rejected(real_postgres_dsn: str):
    parent = uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, parent, TENANT_A)
    finally:
        await conn.close()
    root = await CapabilityTokenService.issue_root(_principal(TENANT_A), parent, ttl_seconds=3600)
    try:
        with pytest.raises(CapabilityDenied):
            await DelegationService.create_delegation(
                tenant_id=TENANT_B, parent_run_id=parent, parent_token_id=root.token_id,
                requested=CapabilityGrant(capabilities={"agent:execute"}),
                parent_agent_id="parent", child_agent_id="executor", node_id="cross",
                actor=_principal(TENANT_B),
            )
    finally:
        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM agent_runs WHERE run_id = $1::uuid", parent)
        finally:
            await conn.close()


@pytest.mark.asyncio
async def test_child_cannot_escalate_capability(real_postgres_dsn: str):
    parent = uuid4()
    child = deterministic_child_run_id(TENANT_A, str(parent), "escalate", 1)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _seed_run(conn, parent, TENANT_A)
            await _seed_run(conn, child, TENANT_A, "SUB_AGENT")
    finally:
        await conn.close()
    limited = Principal(
        tenant_id=TENANT_A, user_id="delegation-user", scopes=["agent:execute"],
        permissions=["agent:execute"], authentication_method="dev",
    )
    root = await CapabilityTokenService.issue_root(limited, parent, ttl_seconds=3600)
    try:
        with pytest.raises(CapabilityDenied) as raised:
            await DelegationService.create_delegation(
                tenant_id=TENANT_A, parent_run_id=parent, parent_token_id=root.token_id,
                requested=CapabilityGrant(capabilities={"external:send"}),
                parent_agent_id="parent", child_agent_id="executor", node_id="escalate",
                actor=_principal(TENANT_A),
            )
        assert raised.value.code == "capability_escalation"
    finally:
        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("DELETE FROM agent_delegations WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = $1", TENANT_A)
                await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [parent, child])
        finally:
            await conn.close()


def test_sub_node_role_validation():
    proposal = {
        "root_goal": "delegate",
        "nodes": [{
            "node_id": "child", "node_type": "sub_agent", "name": "Child",
            "description": "child task", "agent_role": "executor",
            "required_permissions": ["agent:execute"],
        }],
        "edges": [],
    }
    allowed = GraphLimits(allowed_agent_roles={"executor", "researcher"})
    graph_proposal = GraphProposal.model_validate(proposal)
    report = validate_proposal(
        graph_proposal, limits=allowed, allowed_permissions={"agent:execute"},
        graph_id="g", run_id="r", tenant_id="t",
    )
    assert report.valid, report.issues

    denied = validate_proposal(
        graph_proposal, limits=GraphLimits(allowed_agent_roles={"researcher"}),
        allowed_permissions={"agent:execute"}, graph_id="g", run_id="r", tenant_id="t",
    )
    assert any(issue.code == "AGENT_ROLE_NOT_ALLOWED" for issue in denied.issues)
