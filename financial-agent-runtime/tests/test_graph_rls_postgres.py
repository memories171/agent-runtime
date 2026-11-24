"""Task Graph 四张表的 PostgreSQL RLS SQL 层真实测试。"""
from __future__ import annotations

import json
from uuid import uuid4

import asyncpg
import pytest

TENANT_A, TENANT_B = "graph-rls-a", "graph-rls-b"


async def _system(conn, tenant=TENANT_A):
    await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)


async def _seed(conn, graph_id: str, run_id, tenant: str):
    await _system(conn, tenant)
    await conn.execute(
        """INSERT INTO agent_runs (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, metadata)
           VALUES ($1::uuid, $2, 'rls-user', 'rls-session', 'agent', 'rls', 'QUEUED', '{}'::json)
           ON CONFLICT (run_id) DO NOTHING""",
        run_id, tenant,
    )
    await conn.execute(
        """INSERT INTO task_graphs
           (graph_id, tenant_id, run_id, graph_version, graph_status, root_goal,
            acceptance_criteria, max_concurrency, failure_policy, validation_report, definition_json)
           VALUES ($1, $2, $3::uuid, 1, 'RUNNING', 'rls test', '[]'::json, 3, 'CONTINUE_INDEPENDENT',
                   '{}'::json, '{}'::json)
           ON CONFLICT (graph_id) DO NOTHING""",
        graph_id, tenant, run_id,
    )
    await conn.execute(
        """INSERT INTO task_graph_nodes
           (node_id, graph_id, tenant_id, node_key, name, description)
           VALUES (gen_random_uuid(), $1, $2, 'node-1', 'Node 1', 'rls')
           ON CONFLICT (tenant_id, graph_id, node_key) DO NOTHING""",
        graph_id, tenant,
    )
    await conn.execute(
        """INSERT INTO task_graph_edges
           (edge_id, graph_id, tenant_id, edge_key, source_node_id, target_node_id)
           VALUES (gen_random_uuid(), $1, $2, 'edge-1', 'a', 'b')
           ON CONFLICT (tenant_id, graph_id, edge_key) DO NOTHING""",
        graph_id, tenant,
    )
    await conn.execute(
        """INSERT INTO graph_events
           (event_id, graph_id, tenant_id, run_id, sequence_no, event_type)
           VALUES (gen_random_uuid(), $1, $2, $3::uuid, 1, 'GRAPH_STARTED')
           ON CONFLICT (tenant_id, graph_id, sequence_no) DO NOTHING""",
        graph_id, tenant, run_id,
    )


@pytest.mark.asyncio
async def test_graph_tables_rls_cross_tenant(real_postgres_dsn: str):
    conn = await asyncpg.connect(real_postgres_dsn)
    graph_a, graph_b = f"rls-graph-a-{uuid4()}", f"rls-graph-b-{uuid4()}"
    run_a, run_b = uuid4(), uuid4()
    try:
        async with conn.transaction():
            await _seed(conn, graph_a, run_a, TENANT_A)
            await _seed(conn, graph_b, run_b, TENANT_B)

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
            await conn.execute("SELECT set_config('app.system_context', '', true)")
            visible_graphs = await conn.fetchval(
                "SELECT count(*) FROM task_graphs WHERE graph_id = ANY($1::text[])", [graph_a, graph_b]
            )
            visible_nodes = await conn.fetchval(
                "SELECT count(*) FROM task_graph_nodes WHERE graph_id = ANY($1::text[])", [graph_a, graph_b]
            )
            visible_edges = await conn.fetchval(
                "SELECT count(*) FROM task_graph_edges WHERE graph_id = ANY($1::text[])", [graph_a, graph_b]
            )
            visible_events = await conn.fetchval(
                "SELECT count(*) FROM graph_events WHERE graph_id = ANY($1::text[])", [graph_a, graph_b]
            )
            assert (visible_graphs, visible_nodes, visible_edges, visible_events) == (1, 1, 1, 1)

            updated = await conn.execute(
                "UPDATE task_graph_nodes SET status = 'COMPLETED' WHERE graph_id = $1", graph_b
            )
            assert updated == "UPDATE 0"

            await conn.execute("SAVEPOINT before_bad_node")
            with pytest.raises((asyncpg.exceptions.CheckViolationError,
                                asyncpg.exceptions.InsufficientPrivilegeError,
                                asyncpg.exceptions.ForeignKeyViolationError)):
                await conn.execute(
                    """INSERT INTO task_graph_nodes (node_id, graph_id, tenant_id, node_key, name)
                       VALUES (gen_random_uuid(), $1, $2, 'bad', 'bad')""",
                    graph_b, TENANT_A,
                )
            await conn.execute("ROLLBACK TO SAVEPOINT before_bad_node")
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
            await conn.execute("SAVEPOINT before_bad_event")
            with pytest.raises((asyncpg.exceptions.CheckViolationError,
                                asyncpg.exceptions.InsufficientPrivilegeError,
                                asyncpg.exceptions.ForeignKeyViolationError)):
                await conn.execute(
                    """INSERT INTO graph_events
                       (event_id, graph_id, tenant_id, run_id, sequence_no, event_type)
                       VALUES (gen_random_uuid(), $1, $2, $3::uuid, 99, 'GRAPH_STARTED')""",
                    graph_b, TENANT_A, run_b,
                )
            await conn.execute("ROLLBACK TO SAVEPOINT before_bad_event")
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
    finally:
        async with conn.transaction():
            await _system(conn, TENANT_A)
            for table in ("graph_events", "task_graph_edges", "task_graph_nodes", "task_graphs"):
                await conn.execute(f"DELETE FROM {table} WHERE graph_id = ANY($1::text[])", [graph_a, graph_b])
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
        await conn.close()


@pytest.mark.asyncio
async def test_graph_rls_pool_reuse_does_not_leak_tenant(real_postgres_dsn: str):
    graph_a, graph_b = f"rls-pool-a-{uuid4()}", f"rls-pool-b-{uuid4()}"
    run_a, run_b = uuid4(), uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await _seed(conn, graph_a, run_a, TENANT_A)
            await _seed(conn, graph_b, run_b, TENANT_B)
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(real_postgres_dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire() as pooled:
            async with pooled.transaction():
                await pooled.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
                assert await pooled.fetchval("SELECT count(*) FROM task_graphs WHERE graph_id = $1", graph_a) == 1
        async with pool.acquire() as pooled:
            assert await pooled.fetchval("SELECT count(*) FROM task_graphs WHERE graph_id = $1", graph_b) == 0
            assert await pooled.fetchval("SELECT count(*) FROM task_graphs WHERE graph_id = $1", graph_a) == 0
    finally:
        await pool.close()
        conn = await asyncpg.connect(real_postgres_dsn)
        async with conn.transaction():
            await _system(conn, TENANT_A)
            for table in ("graph_events", "task_graph_edges", "task_graph_nodes", "task_graphs"):
                await conn.execute(f"DELETE FROM {table} WHERE graph_id = ANY($1::text[])", [graph_a, graph_b])
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
        await conn.close()
