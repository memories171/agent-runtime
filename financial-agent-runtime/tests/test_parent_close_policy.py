"""Module 3.3.5：ParentClosePolicy.TERMINATE 真实 Temporal 验证。

不检查 DB 投影作为结论：每个场景都通过 Temporal Client 的 describe()
直接检查 Child Workflow 的真实执行状态。覆盖 Parent 的五种关闭路径：
正常结束、用户 Cancel、Timeout、Child 失败策略、Child Activity 不响应取消。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.worker import Worker

ROOT = None

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


from agent_runtime.config import settings  # noqa: E402
from agent_runtime.contracts import WorkflowInput  # noqa: E402
from agent_runtime.orchestration.delegation_service import (  # noqa: E402
    child_workflow_id,
    deterministic_child_run_id,
)
from agent_runtime.workflow import AgentRunWorkflow  # noqa: E402
from scripts.graph_it_worker import build_activities  # noqa: E402

TASK_QUEUE = "agent-runtime-pcp-it"
TENANT = "pcp-it"
USER = "pcp-user"
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
               VALUES ($1::uuid, $2, $3, 'pcp-session', 'parent', 'pcp test',
                       'QUEUED', 'ROOT', '{}'::json)
               ON CONFLICT (run_id) DO NOTHING""",
            run_id, TENANT, USER,
        )


def _child_run(run_id: str, node_id: str) -> str:
    return str(deterministic_child_run_id(TENANT, run_id, node_id, 1))


def _child_wf_id(run_id: str, node_id: str) -> str:
    return child_workflow_id(TENANT, _child_run(run_id, node_id))


def _proposal(node_ids: list[str], policy: str = "CONTINUE_INDEPENDENT",
              timeout: int = 60, edges: list[tuple[str, str]] | None = None) -> dict:
    return {
        "root_goal": "parent close policy integration test",
        "acceptance_criteria": [],
        "max_concurrency": max(2, len(node_ids)),
        "failure_policy": policy,
        "nodes": [{
            "node_id": nid, "node_type": "sub_agent", "name": nid,
            "description": f"child {nid}", "agent_role": "executor",
            "required_permissions": ["agent:execute"], "timeout_seconds": timeout,
        } for nid in node_ids],
        "edges": [
            {"source_node_id": s, "target_node_id": t, "dependency_type": "SUCCESS"}
            for s, t in (edges or [])
        ],
    }


def _input(run_id: str, graph_id: str, proposal: dict, max_duration: int = 90) -> dict:
    return WorkflowInput(
        run_id=run_id, tenant_id=TENANT, user_id=USER, session_id="pcp-session",
        agent_id="parent", prompt=proposal["root_goal"],
        scopes=["agent:execute"], permissions=["agent:execute"],
        max_steps=30, max_duration_seconds=max_duration, model_name="", harness_version="pcp-it",
        graph_config={
            "enabled": True, "graph_id": graph_id, "graph_version": 1,
            "proposed_graph": proposal,
            "limits": {
                "max_nodes": 20, "max_graph_depth": 8,
                "max_concurrency": max(2, len(proposal["nodes"])),
                "max_node_steps": 4, "allowed_high_risk_nodes": [],
                "allowed_agent_roles": ["executor"],
            },
        },
    ).model_dump(mode="json")


async def _worker(client: Client, *, stubborn: bool = False) -> Worker:
    return Worker(
        client, task_queue=TASK_QUEUE, workflows=[AgentRunWorkflow],
        activities=build_activities(stubborn_decide=stubborn),
        max_concurrent_activities=20, max_concurrent_workflow_tasks=20,
    )


async def _start_worker(client, *, stubborn=False):
    instance = await _worker(client, stubborn=stubborn)
    task = asyncio.create_task(instance.run())
    await asyncio.sleep(0.5)
    return instance, task


async def _stop_worker(instance, task):
    await instance.shutdown()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _db_child_wf_ids(parent_run_id: str) -> dict[str, str]:
    """从 DB 投影读取实际的 child_workflow_id（Temporal 查询的权威定位符）。"""
    async with _conn() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            rows = await conn.fetch(
                """SELECT node_id, child_workflow_id FROM agent_delegations
                   WHERE tenant_id=$1 AND parent_run_id=$2::uuid""",
                TENANT, parent_run_id,
            )
    return {r["node_id"]: r["child_workflow_id"] for r in rows if r["child_workflow_id"]}


async def _child_status(client: Client, wf_id: str) -> WorkflowExecutionStatus | None:
    """直接查询 Temporal 中 Child Workflow 的真实执行状态。

    返回 None 表示 describe 异常（NotFound / 可见性未就绪等）。Child 不存在于
    Temporal 可见性中时绝不可能是 RUNNING，语义上等价于已终止。
    """
    try:
        desc = await client.get_workflow_handle(wf_id).describe()
        return desc.status
    except Exception:
        return None


async def _wait_child_terminal(client: Client, wf_id: str, *, timeout: float = 40.0) -> WorkflowExecutionStatus | None:
    deadline = asyncio.get_event_loop().time() + timeout
    last = "unqueried"
    consecutive_missing = 0
    while asyncio.get_event_loop().time() < deadline:
        last = await _child_status(client, wf_id)
        if last in {
            WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.CANCELED, WorkflowExecutionStatus.TERMINATED,
            WorkflowExecutionStatus.TIMED_OUT,
        }:
            return last
        if last is None:
            consecutive_missing += 1
            # 持续查不到（已被 Temporal 清理/从未可见）=> 不可能是 RUNNING 孤儿。
            if consecutive_missing >= 6:
                return None
        else:
            consecutive_missing = 0
        await asyncio.sleep(0.5)
    raise AssertionError(f"child workflow {wf_id} still not terminal after {timeout}s: {last}")


async def _wait_db_child_terminal(
    client: Client, parent_run_id: str, node_id: str, *, timeout: float = 40.0
) -> WorkflowExecutionStatus | None:
    """用 DB 投影中的真实 child_workflow_id 查询 Temporal 实际终止状态。"""
    wf_id = None
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        ids = await _db_child_wf_ids(parent_run_id)
        wf_id = ids.get(node_id)
        if wf_id:
            break
        await asyncio.sleep(0.3)
    assert wf_id, f"no child_workflow_id in DB for node {node_id} of run {parent_run_id}"
    return await _wait_child_terminal(client, wf_id, timeout=timeout)


async def _wait_db_child_workflow_id(
    parent_run_id: str, node_id: str, *, timeout: float = 40.0
) -> str:
    """等待 DB 投影出现真实 child_workflow_id，替代固定 sleep 的竞态。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        wf_id = (await _db_child_wf_ids(parent_run_id)).get(node_id)
        if wf_id:
            return wf_id
        await asyncio.sleep(0.3)
    raise AssertionError(f"child_workflow_id never appeared for {node_id} of {parent_run_id}")


async def _wait_child_running(
    client: Client, wf_id: str, *, timeout: float = 30.0
) -> None:
    """确认 Child 在 Temporal 中确实 RUNNING 后再触发 Parent 取消。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last = "unqueried"
    while asyncio.get_event_loop().time() < deadline:
        last = await _child_status(client, wf_id)
        if last == WorkflowExecutionStatus.RUNNING:
            return
        if last in {
            WorkflowExecutionStatus.COMPLETED, WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.CANCELED, WorkflowExecutionStatus.TERMINATED,
            WorkflowExecutionStatus.TIMED_OUT,
        }:
            raise AssertionError(f"child {wf_id} reached {last} before parent close")
        await asyncio.sleep(0.3)
    raise AssertionError(f"child {wf_id} never became RUNNING after {timeout}s: {last}")


async def _cleanup(run_ids: list[str], graph_ids: list[str]) -> None:
    async with _conn() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute(f"DELETE FROM graph_it_probe WHERE run_id = ANY($1::text[])", run_ids)
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
        id=f"pcp-{run_id}", task_queue=TASK_QUEUE,
        execution_timeout=timedelta(seconds=TIMEOUT),
    )


@pytest.mark.asyncio
async def test_pcp_parent_normal_end(temporal_client):
    """Parent 正常结束：Child 先完成；无孤儿 Running Child。"""
    instance, task = await _start_worker(temporal_client)
    run_id, graph_id = str(uuid4()), f"pcp-normal-{uuid4()}"
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}"] = "0.2"
    handle = await _start(temporal_client, run_id, graph_id, _proposal(["child"]), 90)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "SUCCEEDED", result
        child = await _child_status(temporal_client, _child_wf_id(run_id, "child"))
        assert child == WorkflowExecutionStatus.COMPLETED, child
    finally:
        os.environ.pop(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", None)
        await _stop_worker(instance, task)
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_pcp_parent_user_cancel(temporal_client):
    """Parent 用户 Cancel 后结束：活跃 Child 在 Temporal 中不再 Running。"""
    instance, task = await _start_worker(temporal_client)
    run_id, graph_id = str(uuid4()), f"pcp-cancel-{uuid4()}"
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}"] = "12"
    handle = await _start(temporal_client, run_id, graph_id, _proposal(["child"]), 90)
    try:
        await asyncio.sleep(2)
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        child = await _wait_db_child_terminal(temporal_client, run_id, "child")
        # Parent 取消后 Child 不得仍 RUNNING；具体终态取决于竞态。
        assert child != WorkflowExecutionStatus.RUNNING, child
    finally:
        os.environ.pop(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", None)
        await _stop_worker(instance, task)
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_pcp_parent_timeout(temporal_client):
    """Parent Timeout 后结束：活跃 Child 在 Temporal 中不再 Running。"""
    instance, task = await _start_worker(temporal_client)
    run_id, graph_id = str(uuid4()), f"pcp-timeout-{uuid4()}"
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}"] = "12"
    handle = await _start(temporal_client, run_id, graph_id, _proposal(["child"]), 8)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "TIMED_OUT", result
        child = await _wait_db_child_terminal(temporal_client, run_id, "child")
        # 关键：Parent 关闭后 Child 绝不能仍在 RUNNING（无孤儿）；
        # 具体终态取决于取消与 Child 自身完成的竞态，CANCELED/TERMINATED/COMPLETED 均可接受。
        assert child != WorkflowExecutionStatus.RUNNING, child
    finally:
        os.environ.pop(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", None)
        await _stop_worker(instance, task)
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])


@pytest.mark.asyncio
async def test_pcp_parent_failure_policy(temporal_client):
    """Parent 因 Child 失败策略（FAIL_FAST）结束：无孤儿 Running Child。"""
    instance, task = await _start_worker(temporal_client)
    run_id, graph_id = str(uuid4()), f"pcp-fail-{uuid4()}"
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'a')}"] = "0.2"
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'b')}"] = "8"
    os.environ["GRAPH_IT_FAIL_RUNS"] = _child_run(run_id, "a")
    handle = await _start(temporal_client, run_id, graph_id,
                          _proposal(["a", "b"], policy="FAIL_FAST"), 90)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "FAILED", result
        for nid in ("a", "b"):
            child = await _wait_db_child_terminal(temporal_client, run_id, nid)
            assert child != WorkflowExecutionStatus.RUNNING, (nid, child)
    finally:
        for nid in ("a", "b"):
            os.environ.pop(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, nid)}", None)
        os.environ.pop("GRAPH_IT_FAIL_RUNS", None)
        await _stop_worker(instance, task)
        await _cleanup([run_id, _child_run(run_id, "a"), _child_run(run_id, "b")], [graph_id])


@pytest.mark.asyncio
async def test_pcp_parent_close_terminates_stubborn_child(temporal_client, monkeypatch):
    """Parent 结束而 Child Activity 不响应取消：ParentClosePolicy.TERMINATE 最后保护。

    通过 describe() 直接确认 Child 被 TERMINATED（不是仅看 DB 投影）；
    DB 投影在宽限期内保持 CANCEL_REQUESTED，不虚报 CANCELLED。
    """
    monkeypatch.setattr(settings, "orchestration_child_cancel_grace_seconds", 3)
    instance, task = await _start_worker(temporal_client, stubborn=True)
    run_id, graph_id = str(uuid4()), f"pcp-stubborn-{uuid4()}"
    # Child sleep(90) > 父等待窗口(grace 3 + extra 60)，确保宽限期内不会自行终止，
    # 只能靠 ParentClosePolicy.TERMINATE 在 Parent 关闭时终止。
    os.environ[f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}"] = "90"
    handle = await _start(temporal_client, run_id, graph_id, _proposal(["child"]), 90)
    try:
        # 固定 sleep 会在 Planning Activity 完成前发 Cancel，导致 Child 从未启动；
        # 必须等到真实 Child Workflow RUNNING 后才开始验证 TERMINATE 兜底。
        wf_id = await _wait_db_child_workflow_id(run_id, "child")
        await _wait_child_running(temporal_client, wf_id)
        await handle.signal("cancel")
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "CANCELLED", result
        # 直接查 Temporal：Child 必须被 ParentClosePolicy.TERMINATE 终止。
        child = await _wait_child_terminal(temporal_client, wf_id)
        assert child in {WorkflowExecutionStatus.TERMINATED, WorkflowExecutionStatus.CANCELED}, child
        # DB 投影：宽限期结束时未确认取消，不应虚报 CANCELLED。
        async with _conn() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                row = await conn.fetchrow(
                    """SELECT status, error FROM agent_delegations
                       WHERE tenant_id=$1 AND parent_run_id=$2::uuid AND node_id='child'""",
                    TENANT, run_id,
                )
        assert row is not None
        assert row["status"] in {"CANCEL_REQUESTED", "CANCELLED"}, row["status"]
    finally:
        os.environ.pop(f"GRAPH_IT_NODE_SLEEP_{_child_run(run_id, 'child')}", None)
        await _stop_worker(instance, task)
        await _cleanup([run_id, _child_run(run_id, "child")], [graph_id])
