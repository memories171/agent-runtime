from __future__ import annotations

import json
from uuid import uuid4

import asyncpg
import pytest

INSERT_RUN = """
INSERT INTO agent_runs (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, metadata)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::json)
"""


async def _insert_run(conn, run_id, tenant_id):
    await conn.execute(
        INSERT_RUN,
        run_id,
        tenant_id,
        "user-a",
        f"session-{run_id}",
        "default-agent",
        "rls test",
        "QUEUED",
        json.dumps({}),
    )


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_read_write_and_insert(real_postgres_dsn: str):
    conn = await asyncpg.connect(real_postgres_dsn)
    run_a, run_b = uuid4(), uuid4()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _insert_run(conn, run_a, "tenant-a")
            await _insert_run(conn, run_b, "tenant-b")

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
            rows = await conn.fetch("SELECT run_id FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
            assert [row["run_id"] for row in rows] == [run_a]

            updated = await conn.execute("UPDATE agent_runs SET status = 'RUNNING' WHERE run_id = $1", run_b)
            assert updated == "UPDATE 0"

            with pytest.raises((
                asyncpg.exceptions.CheckViolationError,
                asyncpg.exceptions.InsufficientPrivilegeError,
            )):
                await conn.execute(
                    INSERT_RUN,
                    uuid4(),
                    "tenant-b",
                    "user-a",
                    "session-x",
                    "default-agent",
                    "bad insert",
                    "QUEUED",
                    json.dumps({}),
                )
    finally:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
        await conn.close()


@pytest.mark.asyncio
async def test_rls_connection_pool_reuse_does_not_leak_tenant(real_postgres_dsn: str):
    conn = await asyncpg.connect(real_postgres_dsn)
    run_a, run_b = uuid4(), uuid4()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await _insert_run(conn, run_a, "tenant-a")
            await _insert_run(conn, run_b, "tenant-b")

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
            first = await conn.fetchval("SELECT count(*) FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
            assert first == 1

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-b', true)")
            second = await conn.fetchval("SELECT count(*) FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
            assert second == 1
            row = await conn.fetchrow("SELECT run_id FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
            assert row["run_id"] == run_b
    finally:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [run_a, run_b])
        await conn.close()
