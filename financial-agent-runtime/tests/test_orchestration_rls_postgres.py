"""Orchestration 新表的 PostgreSQL RLS SQL 层真实测试（非 API 层过滤）。"""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

TENANT_A, TENANT_B = "orchestration-rls-a", "orchestration-rls-b"


async def _system(conn, tenant=TENANT_A):
    await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)


async def _tenant(conn, tenant: str):
    await conn.execute("SELECT set_config('app.system_context', '', true)")
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)


async def _seed(conn, suffix: str, tenant: str, root, child, token, delegation, graph_id):
    await _system(conn, tenant)
    await conn.execute(
        """INSERT INTO agent_runs (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
           VALUES ($1::uuid, $2, 'rls-user', 'rls-session', 'agent', 'rls', 'RUNNING', 'ROOT', '{}'::json),
                  ($3::uuid, $2, 'rls-user', 'rls-session', 'agent', 'rls child', 'RUNNING', 'SUB_AGENT', '{}'::json)
           ON CONFLICT (run_id) DO NOTHING""",
        root, tenant, child,
    )
    await conn.execute(
        """INSERT INTO task_graphs
           (graph_id, tenant_id, run_id, graph_version, graph_status, root_goal,
            acceptance_criteria, max_concurrency, failure_policy, validation_report, definition_json)
           VALUES ($1, $2, $3::uuid, 1, 'RUNNING', 'rls goal', '[]'::json, 3, 'CONTINUE_INDEPENDENT',
                   '{}'::json, '{}'::json)
           ON CONFLICT (graph_id) DO NOTHING""",
        graph_id, tenant, root,
    )
    await conn.execute(
        """INSERT INTO task_graph_nodes
           (node_id, graph_id, tenant_id, node_key, name, description)
           VALUES (gen_random_uuid(), $1, $2, 'node-1', 'Node 1', 'orchestration rls')
           ON CONFLICT (tenant_id, graph_id, node_key) DO NOTHING""",
        graph_id, tenant,
    )
    await conn.execute(
        """INSERT INTO capability_tokens
           (token_id, tenant_id, run_id, parent_token_id, subject_run_id, subject_agent_id,
            capabilities, resource_scope, depth, max_depth, expires_at, signature_hash)
           VALUES ($1::uuid, $2, $3::uuid, NULL, $4::uuid, 'researcher',
                   '["agent:execute"]'::jsonb, '{}'::jsonb, 1, 3, now() + interval '1 hour', $5)
           ON CONFLICT (tenant_id, token_id) DO NOTHING""",
        token, tenant, root, child, f"sig-{suffix}",
    )
    await conn.execute(
        """INSERT INTO agent_delegations
           (delegation_id, tenant_id, parent_run_id, child_run_id, parent_agent_id, child_agent_id,
            capability_token_id, delegation_key, status, graph_id, node_id, depth, max_depth, attempt)
           VALUES ($1::uuid, $2, $3::uuid, $4::uuid, 'parent', 'researcher', $5::uuid, $6, 'RUNNING',
                   $7, 'node-1', 1, 3, 1)
           ON CONFLICT (tenant_id, delegation_key) DO NOTHING""",
        delegation, tenant, root, child, token, f"delegation-{suffix}", graph_id,
    )
    await conn.execute(
        """INSERT INTO graph_events
           (event_id, graph_id, tenant_id, run_id, sequence_no, event_type, agent_id, delegation_id, capability_token_id, depth)
           VALUES (gen_random_uuid(), $1, $2, $3::uuid, 1, 'AGENT_DELEGATED', 'researcher', $4::uuid, $5::uuid, 1)
           ON CONFLICT (tenant_id, graph_id, sequence_no) DO NOTHING""",
        graph_id, tenant, root, delegation, token,
    )
    await conn.execute(
        """INSERT INTO run_events
           (event_id, run_id, tenant_id, event_type, payload, parent_run_id, delegation_id, agent_id)
           VALUES (gen_random_uuid(), $1::uuid, $2, 'AGENT_STARTED', '{}'::json, $1::uuid, $3::uuid, 'researcher')""",
        root, tenant, delegation,
    )


async def _cleanup(rows):
    conn = await asyncpg.connect(rows["dsn"])
    try:
        async with conn.transaction():
            await _system(conn, TENANT_A)
            tokens = [str(rows["token_a"]), str(rows["token_b"])]
            delegations = [str(rows["delegation_a"]), str(rows["delegation_b"])]
            runs = [rows["root_a"], rows["child_a"], rows["root_b"], rows["child_b"]]
            await conn.execute("DELETE FROM graph_events WHERE graph_id = ANY($1::text[])", [rows["graph_a"], rows["graph_b"]])
            await conn.execute("DELETE FROM run_events WHERE run_id = ANY($1::uuid[])", runs)
            await conn.execute("DELETE FROM agent_delegations WHERE delegation_id = ANY($1::uuid[])", delegations)
            await conn.execute("DELETE FROM capability_tokens WHERE token_id = ANY($1::uuid[])", tokens)
            await conn.execute("DELETE FROM task_graph_nodes WHERE graph_id = ANY($1::text[])", [rows["graph_a"]])
            await conn.execute("DELETE FROM task_graph_nodes WHERE graph_id = ANY($1::text[])", [rows["graph_b"]])
            await conn.execute("DELETE FROM task_graphs WHERE graph_id = ANY($1::text[])", [rows["graph_a"]])
            await conn.execute("DELETE FROM task_graphs WHERE graph_id = ANY($1::text[])", [rows["graph_b"]])
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", runs)
    finally:
        await conn.close()


def _new_rows(dsn: str):
    suffix = uuid4().hex[:8]
    return {
        "dsn": dsn,
        "graph_a": f"orch-rls-a-{suffix}",
        "graph_b": f"orch-rls-b-{suffix}",
        "root_a": uuid4(), "child_a": uuid4(), "token_a": uuid4(), "delegation_a": uuid4(),
        "root_b": uuid4(), "child_b": uuid4(), "token_b": uuid4(), "delegation_b": uuid4(),
    }


@pytest.mark.asyncio
async def test_orchestration_tables_rls_cross_tenant(real_postgres_dsn: str):
    rows = _new_rows(real_postgres_dsn)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await _seed(conn, "a", TENANT_A, rows["root_a"], rows["child_a"], rows["token_a"], rows["delegation_a"], rows["graph_a"])
            await _seed(conn, "b", TENANT_B, rows["root_b"], rows["child_b"], rows["token_b"], rows["delegation_b"], rows["graph_b"])

        async with conn.transaction():
            await _tenant(conn, TENANT_A)
            visible_tokens = await conn.fetchval(
                "SELECT count(*) FROM capability_tokens WHERE token_id = ANY($1::uuid[])",
                [rows["token_a"], rows["token_b"]],
            )
            visible_delegations = await conn.fetchval(
                "SELECT count(*) FROM agent_delegations WHERE delegation_id = ANY($1::uuid[])",
                [rows["delegation_a"], rows["delegation_b"]],
            )
            visible_graph_events = await conn.fetchval(
                "SELECT count(*) FROM graph_events WHERE delegation_id = ANY($1::uuid[])",
                [rows["delegation_a"], rows["delegation_b"]],
            )
            assert (visible_tokens, visible_delegations, visible_graph_events) == (1, 1, 1)

            updated = await conn.execute(
                "UPDATE agent_delegations SET status = 'COMPLETED' WHERE delegation_id = $1",
                rows["delegation_b"],
            )
            assert updated == "UPDATE 0"

            await conn.execute("SAVEPOINT bad_token")
            with pytest.raises((asyncpg.exceptions.ForeignKeyViolationError,
                                asyncpg.exceptions.InsufficientPrivilegeError,
                                asyncpg.exceptions.CheckViolationError)):
                await conn.execute(
                    """INSERT INTO capability_tokens
                       (token_id, tenant_id, run_id, expires_at, signature_hash)
                       VALUES (gen_random_uuid(), $1, $2::uuid, now() + interval '1 hour', 'bad')""",
                    TENANT_A, rows["root_b"],
                )
            await conn.execute("ROLLBACK TO SAVEPOINT bad_token")

            await conn.execute("SAVEPOINT bad_delegation")
            with pytest.raises((asyncpg.exceptions.ForeignKeyViolationError,
                                asyncpg.exceptions.InsufficientPrivilegeError)):
                await conn.execute(
                    """INSERT INTO agent_delegations
                       (delegation_id, tenant_id, parent_run_id, child_run_id, parent_agent_id,
                        child_agent_id, capability_token_id, delegation_key)
                       VALUES (gen_random_uuid(), $1, $2::uuid, $3::uuid, 'parent', 'researcher', $4::uuid, 'bad')""",
                    TENANT_A, rows["root_b"], rows["child_b"], rows["token_b"],
                )
            await conn.execute("ROLLBACK TO SAVEPOINT bad_delegation")
    finally:
        await conn.close()
        await _cleanup(rows)


@pytest.mark.asyncio
async def test_orchestration_rls_pool_reuse_does_not_leak_tenant(real_postgres_dsn: str):
    rows = _new_rows(real_postgres_dsn)
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await _seed(conn, "a", TENANT_A, rows["root_a"], rows["child_a"], rows["token_a"], rows["delegation_a"], rows["graph_a"])
            await _seed(conn, "b", TENANT_B, rows["root_b"], rows["child_b"], rows["token_b"], rows["delegation_b"], rows["graph_b"])
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(real_postgres_dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire() as pooled:
            async with pooled.transaction():
                await _tenant(pooled, TENANT_A)
                assert await pooled.fetchval(
                    "SELECT count(*) FROM capability_tokens WHERE token_id = $1", rows["token_a"]
                ) == 1
                assert await pooled.fetchval(
                    "SELECT count(*) FROM agent_delegations WHERE delegation_id = $1", rows["delegation_b"]
                ) == 0
        async with pool.acquire() as pooled:
            assert await pooled.fetchval(
                "SELECT count(*) FROM capability_tokens WHERE token_id = ANY($1::uuid[])",
                [rows["token_a"], rows["token_b"]],
            ) == 0
            assert await pooled.fetchval(
                "SELECT count(*) FROM agent_delegations WHERE delegation_id = ANY($1::uuid[])",
                [rows["delegation_a"], rows["delegation_b"]],
            ) == 0
    finally:
        await pool.close()
        await _cleanup(rows)
