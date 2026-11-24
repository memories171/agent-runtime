"""Task Graph Engine 真实集成测试（真实 Temporal Server + 真实 PostgreSQL + Agent Worker）。

使用隔离 task queue ``agent-runtime-graph-it`` 和测试专用慢 Activity；不触碰生产队列。
运行前需启动 docker compose 的 postgres/temporal，并设置 TEST_DATABASE_URL。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager, suppress
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]

_TEMPORAL_IT_ON = os.getenv("RUN_TEMPORAL_IT", "").strip().lower() in {"1", "true", "yes"}
pytestmark = [
    pytest.mark.temporal_it,
    pytest.mark.timeout(900),
    pytest.mark.skipif(not _TEMPORAL_IT_ON, reason="未设置 RUN_TEMPORAL_IT=1：跳过真实 Temporal 集成测试"),
]

_PG_DSN: str | None = None


@pytest.fixture(scope="session", autouse=True)
def _init_pg_dsn(real_postgres_dsn: str) -> None:
    global _PG_DSN
    _PG_DSN = real_postgres_dsn


from agent_runtime.contracts import WorkflowInput  # noqa: E402
from agent_runtime.workflow import AgentRunWorkflow  # noqa: E402
from scripts.graph_it_activities import PROBE_TABLE  # noqa: E402
from scripts.graph_it_worker import GRAPH_IT_TASK_QUEUE, build_activities  # noqa: E402

from temporalio.worker import Worker  # noqa: E402

TENANT = "graph-it-tenant"
USER = "graph-it-user"
TIMEOUT = 180


@asynccontextmanager
async def _conn(dsn: str):
    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()


def _proposal(node_ids, edges=(), policy="CONTINUE_INDEPENDENT", concurrency=3):
    return {
        "root_goal": "Temporal Task Graph integration test",
        "acceptance_criteria": ["all nodes have explicit terminal state"],
        "max_concurrency": concurrency,
        "failure_policy": policy,
        "nodes": [
            {
                "node_id": node_id,
                "node_type": "aggregation" if node_id == "E" else "task",
                "name": node_id,
                "description": f"integration node {node_id}",
                "output_schema": {},
            }
            for node_id in node_ids
        ],
        "edges": [
            {"source_node_id": source, "target_node_id": target, "dependency_type": "SUCCESS"}
            for source, target in edges
        ],
    }


def _input(run_id: str, graph_id: str, proposal: dict, concurrency: int = 3) -> dict:
    payload = WorkflowInput(
        run_id=run_id, tenant_id=TENANT, user_id=USER, session_id="it-session",
        agent_id="graph-it-agent", prompt=proposal["root_goal"],
        scopes=["agent:execute", "runs:read"], permissions=["agent:execute"],
        max_steps=50, max_duration_seconds=900, model_name="", harness_version="graph-it",
        graph_config={
            "enabled": True, "graph_id": graph_id, "graph_version": 1,
            "proposed_graph": proposal,
            "limits": {
                "max_nodes": 40, "max_graph_depth": 12,
                "max_concurrency": concurrency, "max_node_steps": 4,
                "allowed_high_risk_nodes": [],
            },
        },
    )
    return payload.model_dump(mode="json")


async def _seed_run(conn, run_id: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        await conn.execute(
            """INSERT INTO agent_runs
               (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, metadata)
               VALUES ($1::uuid, $2, $3, $4, $5, $6, 'QUEUED', '{}'::json)
               ON CONFLICT (run_id) DO NOTHING""",
            run_id, TENANT, USER, "it-session", "graph-it-agent", "integration test",
        )


async def _graph_row(conn, graph_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        return await conn.fetchrow(
            "SELECT graph_id, graph_version, graph_status, root_goal FROM task_graphs WHERE graph_id = $1",
            graph_id,
        )


async def _node_states(conn, graph_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        rows = await conn.fetch(
            "SELECT node_key, status, attempt_count, result_json FROM task_graph_nodes WHERE graph_id = $1",
            graph_id,
        )
    return {row["node_key"]: row for row in rows}


async def _probe_rows(conn, run_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        return await conn.fetch(
            f"SELECT node_id, phase, ts FROM {PROBE_TABLE} WHERE run_id = $1 ORDER BY ts, node_id",
            run_id,
        )


def _max_overlap(rows) -> int:
    events = []
    for row in rows:
        events.append((row["ts"], 1 if row["phase"] == "START" else -1))
    events.sort(key=lambda item: (item[0], item[1]))
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    return peak


@pytest.fixture(scope="session")
async def probe_table(real_postgres_dsn: str):
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (
                    probe_id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
        )
    finally:
        await conn.close()
    yield real_postgres_dsn


@pytest.fixture(scope="session")
async def worker(temporal_client):
    worker = Worker(
        temporal_client, task_queue=GRAPH_IT_TASK_QUEUE,
        workflows=[AgentRunWorkflow], activities=build_activities(),
        max_concurrent_activities=20, max_concurrent_workflow_tasks=20,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.5)
    yield worker
    await worker.shutdown()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _start_and_wait(client, run_id, graph_id, proposal, concurrency=3):
    async with _conn(_PG_DSN) as conn:
        await _seed_run(conn, run_id)
    handle = await client.start_workflow(
        AgentRunWorkflow.run, _input(run_id, graph_id, proposal, concurrency),
        id=f"graph-it-{run_id}", task_queue=GRAPH_IT_TASK_QUEUE,
    )
    result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
    return handle, result


class TestTemporalGraphIntegration:
    async def test_a_bounded_parallelism(self, temporal_client, worker, probe_table, monkeypatch):
        monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.8")
        monkeypatch.setenv("GRAPH_IT_FAIL_NODES", "")
        run_id, graph_id = str(uuid.uuid4()), f"graph-a-{uuid.uuid4()}"
        proposal = _proposal([f"n{i}" for i in range(10)], concurrency=3)
        _, result = await _start_and_wait(temporal_client, run_id, graph_id, proposal, concurrency=3)

        assert result["status"] == "SUCCEEDED", result
        async with _conn(probe_table) as conn:
            rows = await _probe_rows(conn, run_id)
            graph = await _graph_row(conn, graph_id)
            states = await _node_states(conn, graph_id)
        assert _max_overlap(rows) <= 3, _max_overlap(rows)
        assert _max_overlap(rows) >= 2, "no real overlap observed"
        assert len([r for r in rows if r["phase"] == "END"]) == 10
        assert all(row["status"] == "COMPLETED" for row in states.values())
        assert graph["graph_status"] == "SUCCEEDED"

    async def test_b_dependency_wait(self, temporal_client, worker, probe_table, monkeypatch):
        monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.5")
        monkeypatch.setenv("GRAPH_IT_FAIL_NODES", "")
        run_id, graph_id = str(uuid.uuid4()), f"graph-b-{uuid.uuid4()}"
        proposal = _proposal(
            ["A", "B", "C", "D", "E"],
            edges=[("A", "E"), ("B", "E"), ("C", "E"), ("D", "E")],
            concurrency=4,
        )
        _, result = await _start_and_wait(temporal_client, run_id, graph_id, proposal, concurrency=4)
        assert result["status"] == "SUCCEEDED"
        async with _conn(probe_table) as conn:
            rows = await _probe_rows(conn, run_id)
        started = {r["node_id"]: r["ts"] for r in rows if r["phase"] == "START"}
        ended = {r["node_id"]: r["ts"] for r in rows if r["phase"] == "END"}
        assert all(started["E"] >= ended[node] for node in ["A", "B", "C", "D"])

    @pytest.mark.parametrize("policy,failed,expected", [
        ("FAIL_FAST", "A", "FAILED"),
        ("CONTINUE_INDEPENDENT", "A", "FAILED"),
        ("MANUAL_INTERVENTION", "A", "FAILED"),
    ])
    async def test_c_failure_policies(self, temporal_client, worker, probe_table, monkeypatch,
                                      policy, failed, expected):
        monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.3")
        monkeypatch.setenv("GRAPH_IT_FAIL_NODES", failed)
        run_id, graph_id = str(uuid.uuid4()), f"graph-c-{uuid.uuid4()}"
        proposal = _proposal(
            ["A", "B", "C", "D", "E"],
            edges=[("A", "E"), ("B", "E")],
            policy=policy, concurrency=4,
        )
        if policy == "MANUAL_INTERVENTION":
            async with _conn(probe_table) as conn:
                await _seed_run(conn, run_id)
            handle = await temporal_client.start_workflow(
                AgentRunWorkflow.run, _input(run_id, graph_id, proposal, 4),
                id=f"graph-it-{run_id}", task_queue=GRAPH_IT_TASK_QUEUE,
            )
            # 条件等待：MANUAL_INTERVENTION 需等 A 失败后 graph_status 投影到 WAITING。
            # 全量负载下 activity 调度会变慢，用明确条件 + 合理超时 + 失败诊断，
            # 而非固定 sleep 次数；同时检查 workflow 是否异常关闭，避免误判产品问题。
            async def _wait_waiting():
                last = None
                while True:
                    async with _conn(probe_table) as conn:
                        graph = await _graph_row(conn, graph_id)
                    last = graph["graph_status"] if graph else None
                    if last == "WAITING":
                        return
                    await asyncio.sleep(0.2)

            try:
                await asyncio.wait_for(_wait_waiting(), timeout=TIMEOUT)
            except asyncio.TimeoutError as exc:
                async with _conn(probe_table) as conn:
                    states = await _node_states(conn, graph_id)
                raise AssertionError(
                    f"graph did not reach WAITING within {TIMEOUT}s; "
                    f"last_status={last!r}, node_states={states}"
                ) from exc
            await handle.signal("resume", {"command_id": f"manual-{run_id}"})
            result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        else:
            _, result = await _start_and_wait(temporal_client, run_id, graph_id, proposal, concurrency=4)
        assert result["status"] == expected
        async with _conn(probe_table) as conn:
            states = await _node_states(conn, graph_id)
        assert states["A"]["status"] == "FAILED"
        if policy == "FAIL_FAST":
            assert states["E"]["status"] == "CANCELLED"
        if policy == "CONTINUE_INDEPENDENT":
            assert states["B"]["status"] == "COMPLETED"
            assert states["E"]["status"] == "SKIPPED"
        if policy == "MANUAL_INTERVENTION":
            assert states["E"]["status"] == "SKIPPED"

    async def test_e_pause_resume(self, temporal_client, worker, probe_table, monkeypatch):
        monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "1.2")
        monkeypatch.setenv("GRAPH_IT_FAIL_NODES", "")
        run_id, graph_id = str(uuid.uuid4()), f"graph-e-{uuid.uuid4()}"
        proposal = _proposal([f"p{i}" for i in range(8)], concurrency=3)
        async with _conn(probe_table) as conn:
            await _seed_run(conn, run_id)
        handle = await temporal_client.start_workflow(
            AgentRunWorkflow.run, _input(run_id, graph_id, proposal, 3),
            id=f"graph-it-{run_id}", task_queue=GRAPH_IT_TASK_QUEUE,
        )
        async with _conn(probe_table) as conn:
            for _ in range(100):
                rows = await _probe_rows(conn, run_id)
                if len([r for r in rows if r["phase"] == "START"]) >= 3:
                    break
                await asyncio.sleep(0.1)
        await handle.signal("pause", {"command_id": f"pause-{run_id}"})
        async with _conn(probe_table) as conn:
            for _ in range(100):
                graph = await _graph_row(conn, graph_id)
                if graph and graph["graph_status"] == "PAUSED":
                    break
                await asyncio.sleep(0.1)
            assert graph["graph_status"] == "PAUSED"
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT)
            checkpoints = await conn.fetchval(
                "SELECT count(*) FROM run_checkpoints WHERE run_id = $1::uuid", run_id
            )
            assert checkpoints >= 1
        await handle.signal("resume", {"command_id": f"resume-{run_id}"})
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "SUCCEEDED"
        async with _conn(probe_table) as conn:
            rows = await _probe_rows(conn, run_id)
        starts = [r for r in rows if r["phase"] == "START"]
        ends = [r for r in rows if r["phase"] == "END"]
        assert len(starts) == len(ends) == 8, "duplicated node execution across pause/resume"

    async def test_d_worker_crash_recovery(self, temporal_client, probe_table, monkeypatch):
        monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.4")
        monkeypatch.setenv("GRAPH_IT_FAIL_NODES", "")
        run_id, graph_id = str(uuid.uuid4()), f"graph-d-{uuid.uuid4()}"
        node_ids = [f"n{i:02d}" for i in range(20)]
        edges = [(f"n{i:02d}", f"n{i+10:02d}") for i in range(10)]
        edges += [("n10", "E"), ("n11", "E"), ("n12", "E"), ("n13", "E"), ("n14", "E"),
                  ("n15", "E"), ("n16", "E"), ("n17", "E"), ("n18", "E"), ("n19", "E")]
        proposal = _proposal(node_ids + ["E"], edges, concurrency=5)

        async with _conn(probe_table) as conn:
            await _seed_run(conn, run_id)

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src")])
        env["GRAPH_IT_SLEEP_SECONDS"] = "0.4"
        env["GRAPH_IT_FAIL_NODES"] = ""
        proc = subprocess.Popen([sys.executable, "-m", "scripts.graph_it_worker"], env=env)
        try:
            handle = await temporal_client.start_workflow(
                AgentRunWorkflow.run, _input(run_id, graph_id, proposal, 5),
                id=f"graph-it-{run_id}", task_queue=GRAPH_IT_TASK_QUEUE,
            )
            async with _conn(probe_table) as conn:
                for _ in range(200):
                    rows = await _probe_rows(conn, run_id)
                    if len([r for r in rows if r["phase"] == "END"]) >= 4:
                        break
                    await asyncio.sleep(0.1)
            proc.kill()
            proc.wait()
            await asyncio.sleep(1.0)

            proc2 = subprocess.Popen([sys.executable, "-m", "scripts.graph_it_worker"], env=env)
            try:
                result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
            finally:
                proc2.terminate()
                proc2.wait()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()

        assert result["status"] == "SUCCEEDED"
        async with _conn(probe_table) as conn:
            graph = await _graph_row(conn, graph_id)
            states = await _node_states(conn, graph_id)
            rows = await _probe_rows(conn, run_id)
        assert graph["graph_id"] == graph_id
        assert graph["graph_version"] == 1
        assert graph["graph_status"] == "SUCCEEDED"
        assert all(row["status"] == "COMPLETED" for row in states.values())
        ends_by_node = {}
        for row in rows:
            if row["phase"] == "END":
                ends_by_node[row["node_id"]] = ends_by_node.get(row["node_id"], 0) + 1
        assert all(count == 1 for count in ends_by_node.values()), ends_by_node
