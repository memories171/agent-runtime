"""Module 3.3.5：Parent/Child Timeout & Cancellation 真实 Temporal 集成测试。

不 Mock Child Handle：全部通过真实 Temporal Server + Worker + AgentRunWorkflow 验证。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from temporalio.client import Client
from temporalio.worker import Worker

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
from agent_runtime.orchestration.delegation_service import deterministic_child_run_id  # noqa: E402
from agent_runtime.workflow import AgentRunWorkflow  # noqa: E402
from scripts.graph_it_activities import PROBE_TABLE  # noqa: E402
from scripts.graph_it_worker import build_activities  # noqa: E402

TASK_QUEUE = "agent-runtime-timeout-cancel-it"
TASK_QUEUE_RESTART = "agent-runtime-timeout-cancel-restart-it"
TENANT = "timeout-cancel-it"
USER = "timeout-cancel-user"
TIMEOUT = 150


@asynccontextmanager
async def _conn():
    connection = await asyncpg.connect(_PG_DSN)
    try:
        yield connection
    finally:
        await connection.close()


async def _seed_parent(conn, run_id: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        await conn.execute(
            """INSERT INTO agent_runs
               (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
               VALUES ($1::uuid, $2, $3, 'tc-session', 'parent', 'timeout cancel test',
                       'QUEUED', 'ROOT', '{}'::json)
               ON CONFLICT (run_id) DO NOTHING""",
            run_id, TENANT, USER,
        )


def _child_run(run_id: str, node_id: str) -> str:
    return str(deterministic_child_run_id(TENANT, run_id, node_id, 1))


def _proposal(
    node_ids: list[str], policy: str = "CONTINUE_INDEPENDENT",
    timeout: int = 60, timeouts: dict[str, int] | None = None,
    edges: list[tuple[str, str]] | None = None,
) -> dict:
    timeouts = timeouts or {}
    nodes = [{
        "node_id": nid, "node_type": "sub_agent", "name": nid,
        "description": f"child {nid}", "agent_role": "executor",
        "required_permissions": ["agent:execute"],
        "timeout_seconds": timeouts.get(nid, timeout),
    } for nid in node_ids]
    return {
        "root_goal": "timeout/cancel integration test",
        "acceptance_criteria": [],
        "max_concurrency": max(2, len(node_ids)),
        "failure_policy": policy,
        "nodes": nodes,
        "edges": [
            {"source_node_id": s, "target_node_id": t, "dependency_type": "SUCCESS"}
            for s, t in (edges or [])
        ],
    }


def _input(run_id: str, graph_id: str, proposal: dict, max_duration: int = 90) -> dict:
    return WorkflowInput(
        run_id=run_id, tenant_id=TENANT, user_id=USER, session_id="tc-session",
        agent_id="parent", prompt=proposal["root_goal"],
        scopes=["agent:execute"], permissions=["agent:execute"],
        max_steps=30, max_duration_seconds=max_duration, model_name="", harness_version="tc-it",
        graph_config={
            "enabled": True, "graph_id": graph_id, "graph_version": 1,
            "proposed_graph": proposal,
            "limits": {
                "max_nodes": 20, "max_graph_depth": 8, "max_concurrency": max(2, len(proposal["nodes"])),
                "max_node_steps": 4, "allowed_high_risk_nodes": [],
                "allowed_agent_roles": ["executor"],
            },
        },
    ).model_dump(mode="json")


async def _worker(client: Client, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client, task_queue=task_queue, workflows=[AgentRunWorkflow],
        activities=build_activities(),
        max_concurrent_activities=20, max_concurrent_workflow_tasks=20,
    )


@pytest.fixture(scope="session")
async def worker(temporal_client):
    instance = await _worker(temporal_client)
    task = asyncio.create_task(instance.run())
    await asyncio.sleep(0.5)
    yield instance
    await instance.shutdown()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _wait_child_started(child_run_ids: list[str], timeout: float = 25.0) -> None:
    """轮询探测表直到指定 Child 的 decision activity 进入 START，确保 Cancel 时 Child 已活跃。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with _conn() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    f"SELECT run_id FROM {PROBE_TABLE} WHERE run_id = ANY($1::text[]) AND phase = 'START'",
                    child_run_ids,
                )
            if len({r["run_id"] for r in rows}) >= len(child_run_ids):
                return
        await asyncio.sleep(0.3)
    raise AssertionError(f"children did not start within {timeout}s: {child_run_ids}")


async def _wait_child_done(child_run_ids: list[str], timeout: float = 25.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with _conn() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    f"SELECT run_id FROM {PROBE_TABLE} WHERE run_id = ANY($1::text[]) AND phase = 'END'",
                    child_run_ids,
                )
            if len({r["run_id"] for r in rows}) >= len(child_run_ids):
                return
        await asyncio.sleep(0.3)
    raise AssertionError(f"children did not finish within {timeout}s: {child_run_ids}")


async def _wait_delegation_terminal(parent_run_id: str, node_id: str, timeout: float = 20.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with _conn() as conn:
            rows = await _delegations(conn, parent_run_id)
        row = rows.get(node_id)
        if row and row["status"] in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return
        await asyncio.sleep(0.3)
    raise AssertionError(f"delegation {node_id} did not reach terminal within {timeout}s")


async def _delegations(conn, parent_run_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        return {
            row["node_id"]: row for row in await conn.fetch(
                """SELECT delegation_id, node_id, status, child_workflow_id, child_run_id
                   FROM agent_delegations WHERE tenant_id = $1 AND parent_run_id = $2::uuid""",
                TENANT, parent_run_id,
            )
        }


async def _cleanup(run_ids: list[str], graph_ids: list[str]) -> None:
    async with _conn() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute(f"DELETE FROM {PROBE_TABLE} WHERE run_id = ANY($1::text[])", run_ids)
            await conn.execute("DELETE FROM graph_events WHERE graph_id = ANY($1::text[])", graph_ids)
            await conn.execute("DELETE FROM run_events WHERE run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM run_checkpoints WHERE run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM context_packs WHERE run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM agent_delegations WHERE parent_run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM capability_tokens WHERE run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM task_graph_nodes WHERE graph_id = ANY($1::text[])", graph_ids)
            await conn.execute("DELETE FROM task_graphs WHERE graph_id = ANY($1::text[])", graph_ids)
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", run_ids)


async def _start(temporal_client, run_id, graph_id, proposal, max_duration):
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    return await temporal_client.start_workflow(
        AgentRunWorkflow.run, _input(run_id, graph_id, proposal, max_duration),
        id=f"tc-{run_id}", task_queue=TASK_QUEUE,
        execution_timeout=timedelta(seconds=TIMEOUT),
    )


@pytest.mark.asyncio
async def test_parent_cancel_cascades_and_preserves_completed(temporal_client, worker, monkeypatch):
    """三个 Child：一个已完成、一个运行中、一个尚未启动；Parent Cancel。
    已完成的不被改写；运行中的被取消；取消后不再启动新的 Child。"""
    run_id, graph_id = str(uuid4()), f"tc-cancel-{uuid4()}"
    proposal = _proposal(["fast", "slow", "deferred"], edges=[("slow", "deferred")])
    proposal["max_concurrency"] = 2
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'fast')}", "0.1")
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'slow')}", "40")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 90)
    try:
        # 等 fast 真正完成、slow 进入 activity START；deferred 依赖 slow 尚未启动。
        await _wait_child_started([_child_run(run_id, "slow")])
        await _wait_delegation_terminal(run_id, "fast")
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        assert rows["fast"]["status"] == "COMPLETED", rows["fast"]["status"]
        assert rows["slow"]["status"] == "CANCELLED", rows["slow"]["status"]
        # deferred 依赖 slow 且 slow 被取消：不启动新 Child。
        assert "deferred" not in rows or rows["deferred"]["status"] != "COMPLETED"
    finally:
        await _cleanup([run_id] + [_child_run(run_id, n) for n in ["fast", "slow", "deferred"]], [graph_id])


@pytest.mark.asyncio
async def test_repeated_cancel_is_idempotent(temporal_client, worker, monkeypatch):
    run_id, graph_id = str(uuid4()), f"tc-dup-cancel-{uuid4()}"
    proposal = _proposal(["child"])
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", "12")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 90)
    try:
        await _wait_child_started([_child_run(run_id, "child")])
        await handle.signal("cancel")
        await handle.signal("cancel")
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        assert rows["child"]["status"] == "CANCELLED"
    finally:
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_parent_timeout_stops_active_children(temporal_client, worker, monkeypatch):
    """Parent 达到 max_duration：进入 TIMED_OUT 终态，所有活跃 Child 停止。"""
    run_id, graph_id = str(uuid4()), f"tc-timeout-{uuid4()}"
    proposal = _proposal(["child-a", "child-b"])
    for nid in ("child-a", "child-b"):
        monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, nid)}", "60")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 12)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "TIMED_OUT", result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        for nid in ("child-a", "child-b"):
            assert rows[nid]["status"] in {"CANCELLED", "TIMED_OUT"}, rows[nid]["status"]
    finally:
        await _cleanup([run_id] + [_child_run(run_id, n) for n in ["child-a", "child-b"]], [graph_id])


@pytest.mark.asyncio
async def test_single_child_timeout_does_not_cancel_others(temporal_client, worker, monkeypatch):
    """单个 Child 超时仅影响自身；其他 Child 继续完成；FailurePolicy 生效。"""
    run_id, graph_id = str(uuid4()), f"tc-child-timeout-{uuid4()}"
    proposal = _proposal(["slow", "fast"], policy="CONTINUE_INDEPENDENT", timeouts={"slow": 12, "fast": 90})
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'slow')}", "60")
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'fast')}", "0.2")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 120)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] in {"FAILED", "SUCCEEDED"}, result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        assert rows["slow"]["status"] == "TIMED_OUT", rows["slow"]["status"]
        assert rows["fast"]["status"] == "COMPLETED", rows["fast"]["status"]
    finally:
        await _cleanup([run_id] + [_child_run(run_id, n) for n in ["slow", "fast"]], [graph_id])


@pytest.mark.asyncio
async def test_cancel_after_child_complete_does_not_overwrite(temporal_client, worker, monkeypatch):
    """Child 已完成后 Parent Cancel，不改写 COMPLETED 结果。"""
    run_id, graph_id = str(uuid4()), f"tc-complete-cancel-{uuid4()}"
    # fast 先完成；keeper(独立、慢) 保证 Cancel 时 Parent 仍活跃。
    proposal = _proposal(["fast", "keeper"])
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'fast')}", "0.1")
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'keeper')}", "30")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 90)
    try:
        await _wait_delegation_terminal(run_id, "fast")
        await _wait_child_started([_child_run(run_id, "keeper")])
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        assert rows["fast"]["status"] == "COMPLETED", rows["fast"]["status"]
        assert rows["keeper"]["status"] == "CANCELLED", rows["keeper"]["status"]
    finally:
        await _cleanup([run_id] + [_child_run(run_id, n) for n in ["fast", "keeper"]], [graph_id])


@pytest.mark.asyncio
async def test_worker_restart_durable_timer_recovers_timeout(temporal_client, monkeypatch):
    """Worker 重启后 Durable Timer 恢复：Parent 仍在 deadline 到期时 TIMED_OUT。"""
    run_id, graph_id = str(uuid4()), f"tc-restart-timer-{uuid4()}"
    proposal = _proposal(["child"])
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", "60")
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    w1 = await _worker(temporal_client, TASK_QUEUE_RESTART)
    t1 = asyncio.create_task(w1.run())
    await asyncio.sleep(0.5)
    handle = await temporal_client.start_workflow(
        AgentRunWorkflow.run, _input(run_id, graph_id, proposal, 12),
        id=f"tc-restart-{run_id}", task_queue=TASK_QUEUE_RESTART,
        execution_timeout=timedelta(seconds=TIMEOUT),
    )
    try:
        await asyncio.sleep(4)
        await w1.shutdown()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(t1, timeout=10)
        if not t1.done():
            t1.cancel()
            with suppress(asyncio.CancelledError):
                await t1
        w2 = await _worker(temporal_client, TASK_QUEUE_RESTART)
        t2 = asyncio.create_task(w2.run())
        await asyncio.sleep(0.5)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
            assert result["status"] == "TIMED_OUT", result
        finally:
            await w2.shutdown()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(t2, timeout=10)
            if not t2.done():
                t2.cancel()
                with suppress(asyncio.CancelledError):
                    await t2
    finally:
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_cancel_wins_when_cancel_and_timeout_overlap(temporal_client, worker, monkeypatch):
    """Cancel 与 Timeout 同时/接近同时发生：用户 Cancel 优先，终态为 CANCELLED。"""
    run_id, graph_id = str(uuid4()), f"tc-cancel-priority-{uuid4()}"
    proposal = _proposal(["child"])
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", "40")
    # Parent deadline=20s；在 child 启动后立刻 Cancel（远早于 deadline），
    # 验证 Cancel 优先于仍在 pending 的 Durable Timeout Timer。
    handle = await _start(temporal_client, run_id, graph_id, proposal, 20)
    try:
        await _wait_child_started([_child_run(run_id, "child")])
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
    finally:
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_no_new_child_started_after_cancel(temporal_client, worker, monkeypatch):
    """取消后不再启动新 Child：max_concurrency=1 串行，b 在 a 仍运行时不应被创建。"""
    run_id, graph_id = str(uuid4()), f"tc-no-new-child-{uuid4()}"
    proposal = _proposal(["a", "b"], policy="CONTINUE_INDEPENDENT")
    proposal["max_concurrency"] = 1
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'a')}", "30")
    monkeypatch.setenv(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'b')}", "0.1")
    handle = await _start(temporal_client, run_id, graph_id, proposal, 90)
    try:
        await _wait_child_started([_child_run(run_id, "a")])
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
        assert "b" not in rows, f"new child started after cancel: {list(rows)}"
    finally:
        await _cleanup([run_id, _child_run(run_id, "a"), _child_run(run_id, "b")], [graph_id])
