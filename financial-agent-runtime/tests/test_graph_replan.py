"""Replan 版本保留的真实数据库测试。"""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from agent_runtime.graph.models import GraphLimits, GraphProposal
from agent_runtime.graph.service import load_definition, upsert_definition
from agent_runtime.graph.validate import build_definition, validate_proposal

TENANT = "graph-replan-tenant"


def _definition(graph_id: str, run_id: str, version: int, goal: str):
    proposal = {
        "root_goal": goal,
        "nodes": [{"node_id": "a", "name": "A", "description": "first"}],
        "edges": [],
    }
    report = validate_proposal(
        GraphProposal.model_validate(proposal), limits=GraphLimits(), allowed_permissions=set(),
        graph_id=graph_id, run_id=run_id, tenant_id=TENANT, graph_version=version,
    )
    assert report.valid, report.issues
    return build_definition(
        GraphProposal.model_validate(proposal), report,
        graph_id=graph_id, run_id=run_id, tenant_id=TENANT, graph_version=version,
    )


@pytest.mark.asyncio
async def test_replan_preserves_previous_version(real_postgres_dsn: str):
    graph_id, run_id = f"replan-{uuid4()}", str(uuid4())
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute(
                """INSERT INTO agent_runs (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, metadata)
                   VALUES ($1::uuid, $2, 'replan-user', 'replan-session', 'agent', 'replan', 'QUEUED', '{}'::json)""",
                run_id, TENANT,
            )
    finally:
        await conn.close()

    v1 = _definition(graph_id, run_id, 1, "original goal")
    v2 = _definition(graph_id, run_id, 2, "replanned goal")
    await upsert_definition(v1)
    await upsert_definition(v2)

    restored_v1 = await load_definition(TENANT, graph_id, 1)
    restored_v2 = await load_definition(TENANT, graph_id, 2)
    assert restored_v1 is not None and restored_v1.root_goal == "original goal"
    assert restored_v2 is not None and restored_v2.root_goal == "replanned goal"

    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            count = await conn.fetchval(
                "SELECT count(*) FROM task_graph_versions WHERE tenant_id = $1 AND graph_id = $2",
                TENANT, graph_id,
            )
            current = await conn.fetchval(
                "SELECT graph_version FROM task_graphs WHERE tenant_id = $1 AND graph_id = $2",
                TENANT, graph_id,
            )
    finally:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("DELETE FROM task_graph_versions WHERE tenant_id = $1 AND graph_id = $2", TENANT, graph_id)
            for table in ("graph_events", "task_graph_edges", "task_graph_nodes", "task_graphs"):
                await conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1 AND graph_id = $2", TENANT, graph_id)
            await conn.execute("DELETE FROM agent_runs WHERE tenant_id = $1 AND run_id = $2::uuid", TENANT, run_id)
        await conn.close()
    assert count == 2
    assert current == 2
