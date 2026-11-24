"""Sub-Agent Orchestration 真实 Temporal 集成测试。"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from uuid import uuid4, uuid5

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


from agent_runtime.activities_context import _persistence as context_persistence  # noqa: E402
from agent_runtime.context.pack import (  # noqa: E402
    ContextItem, ContextMetadata, ContextPack, GoalContext, HistoricalContext, Provenance,
)
from agent_runtime.contracts import Principal, WorkflowInput  # noqa: E402
from agent_runtime.db import session_for_tenant  # noqa: E402
from agent_runtime.orchestration.activities import create_delegation_activity  # noqa: E402
from agent_runtime.orchestration.delegation_service import (  # noqa: E402
    ORCHESTRATION_NAMESPACE,
    deterministic_child_run_id,
)
from agent_runtime.orchestration.token_service import CapabilityTokenService  # noqa: E402
from agent_runtime.workflow import AgentRunWorkflow  # noqa: E402
from scripts.graph_it_activities import PROBE_TABLE  # noqa: E402
from scripts.graph_it_worker import build_activities  # noqa: E402

TASK_QUEUE = "agent-runtime-subagent-it"
TASK_QUEUE_RESTART = "agent-runtime-subagent-restart-it"
TENANT = "subagent-it-tenant"
USER = "subagent-it-user"
TIMEOUT = 180


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
               VALUES ($1::uuid, $2, $3, 'subagent-session', 'parent', 'subagent integration',
                       'QUEUED', 'ROOT', '{}'::json)
               ON CONFLICT (run_id) DO NOTHING""",
            run_id, TENANT, USER,
        )


async def _seed_parent_context_pack(run_id: str, pack_id: str) -> None:
    pack = ContextPack(
        metadata=ContextMetadata(
            context_pack_id=pack_id, run_id=run_id, tenant_id=TENANT, agent_id="parent",
        ),
        goal=GoalContext(original_user_goal=ContextItem(
            value="Parent task with secret history",
            provenance=Provenance(source_type="system_instruction"),
        )),
        historical=HistoricalContext(recent_events=[ContextItem(
            value="PARENT_SECRET_SENTINEL",
            provenance=Provenance(source_type="tool_result", source_id="parent-secret"),
        )]),
    )
    async with session_for_tenant(TENANT) as session:
        await context_persistence.save_pack(session, pack)
        await session.commit()


def _proposal(
    node_ids: list[str], edges: list[tuple[str, str]], policy: str = "CONTINUE_INDEPENDENT",
    context_references: list[dict] | None = None,
) -> dict:
    nodes = []
    for node_id in node_ids:
        if node_id.startswith("final"):
            nodes.append({
                "node_id": node_id, "node_type": "aggregation", "name": node_id,
                "description": "aggregate child results", "dependencies": [src for src, dst in edges if dst == node_id],
                "required_permissions": [],
            })
        else:
            nodes.append({
                "node_id": node_id, "node_type": "sub_agent", "name": node_id,
                "description": f"child agent {node_id}", "agent_role": "executor",
                "required_permissions": ["agent:execute"], "timeout_seconds": 120,
                "context_references": list(context_references or []),
            })
    return {
        "root_goal": "Sub-Agent integration test",
        "acceptance_criteria": ["children complete with independent tokens"],
        "max_concurrency": 2,
        "failure_policy": policy,
        "nodes": nodes,
        "edges": [
            {"source_node_id": source, "target_node_id": target, "dependency_type": "SUCCESS"}
            for source, target in edges
        ],
    }


def _input(run_id: str, graph_id: str, proposal: dict) -> dict:
    return WorkflowInput(
        run_id=run_id, tenant_id=TENANT, user_id=USER, session_id="subagent-session",
        agent_id="parent", prompt=proposal["root_goal"],
        scopes=["agent:execute", "runs:read"], permissions=["agent:execute", "runs:read"],
        max_steps=30, max_duration_seconds=600, model_name="", harness_version="subagent-it",
        graph_config={
            "enabled": True, "graph_id": graph_id, "graph_version": 1,
            "proposed_graph": proposal,
            "limits": {
                "max_nodes": 20, "max_graph_depth": 8, "max_concurrency": 2,
                "max_node_steps": 4, "allowed_high_risk_nodes": [],
                "allowed_agent_roles": ["executor", "researcher"],
            },
        },
    ).model_dump(mode="json")


async def _worker(client: Client, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client, task_queue=task_queue,
        workflows=[AgentRunWorkflow], activities=build_activities(),
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


async def _delegations(conn, parent_run_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        return await conn.fetch(
            """SELECT delegation_id, child_run_id, capability_token_id, child_workflow_id,
                      status, result_summary, node_id, updated_at
               FROM agent_delegations WHERE tenant_id = $1 AND parent_run_id = $2::uuid
               ORDER BY node_id""",
            TENANT, parent_run_id,
        )


async def _node_rows(conn, graph_id: str):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        return {
            row["node_key"]: row for row in await conn.fetch(
                "SELECT node_key, status, result_json FROM task_graph_nodes WHERE graph_id = $1", graph_id
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
            await conn.execute("DELETE FROM tool_invocations WHERE run_id = ANY($1::uuid[])", run_ids)
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", run_ids)


@pytest.mark.asyncio
async def test_subagent_success_parallel_and_result_aggregation(temporal_client, worker, monkeypatch):
    monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.5")
    run_id, graph_id = str(uuid4()), f"sub-graph-{uuid4()}"
    parent_pack_id = f"parent-context-{run_id}"
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    await _seed_parent_context_pack(run_id, parent_pack_id)
    proposal = _proposal(["child-a", "child-b", "child-c", "final-1"], [
        ("child-a", "final-1"), ("child-b", "final-1"), ("child-c", "final-1"),
    ], context_references=[{
        "context_pack_id": parent_pack_id, "run_id": run_id,
        "tenant_id": TENANT, "include_summary": True,
    }])
    try:
        handle = await temporal_client.start_workflow(
            AgentRunWorkflow.run, _input(run_id, graph_id, proposal),
            id=f"subagent-it-{run_id}", task_queue=TASK_QUEUE,
            execution_timeout=timedelta(seconds=TIMEOUT),
        )
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "SUCCEEDED"

        async with _conn() as conn:
            rows = await _delegations(conn, run_id)
            nodes = await _node_rows(conn, graph_id)
        assert len(rows) == 3
        assert all(row["status"] == "COMPLETED" for row in rows)
        assert all(row["updated_at"] is not None for row in rows)
        assert len({row["child_run_id"] for row in rows}) == 3
        assert len({row["capability_token_id"] for row in rows}) == 3
        root_token = None
        async with _conn() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                root_token = await conn.fetchval(
                    "SELECT token_id FROM capability_tokens WHERE run_id = $1::uuid AND subject_agent_id = 'root'",
                    run_id,
                )
        assert root_token not in {row["capability_token_id"] for row in rows}
        assert nodes["final-1"]["status"] == "COMPLETED"

        child_runs = [str(row["child_run_id"]) for row in rows]
        async with _conn() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                packs = await conn.fetch(
                    "SELECT run_id, pack_json FROM context_packs WHERE run_id = ANY($1::uuid[])",
                    child_runs,
                )
        assert len(packs) >= len(child_runs)
        for row in packs:
            pack = json.loads(row["pack_json"]) if isinstance(row["pack_json"], str) else row["pack_json"]
            metadata = pack["metadata"]
            assert metadata["parent_run_id"] == run_id
            assert metadata["delegation_id"] in {str(item["delegation_id"]) for item in rows}
            assert metadata["context_source"] == "child_projection"
            assert parent_pack_id in metadata["source_context_pack_ids"]
            assert "PARENT_SECRET_SENTINEL" not in json.dumps(pack, ensure_ascii=False)

        summaries = [
            json.loads(row["result_summary"]) if isinstance(row["result_summary"], str) else (row["result_summary"] or {})
            for row in rows
        ]
        assert all(item["status"] == "SUCCEEDED" for item in summaries)
        assert all({"child_run_id", "delegation_id", "result_summary", "artifact_refs", "error", "execution_metadata"} <= set(item) for item in summaries)

        child_runs = [str(row["child_run_id"]) for row in rows]
        async with _conn() as conn:
            async with conn.transaction():
                events = await conn.fetch(
                    f"SELECT run_id, phase, ts FROM {PROBE_TABLE} WHERE run_id = ANY($1::text[]) ORDER BY ts",
                    child_runs,
                )
        assert len(events) == 6
        intervals = []
        starts: dict[str, object] = {}
        for event in events:
            key = str(event["run_id"])
            if event["phase"] == "START":
                starts[key] = event["ts"]
            else:
                intervals.append((starts[key], event["ts"]))
        peak = max(
            sum(start <= point < end for start, end in intervals)
            for point in [item[0] for item in intervals]
        )
        assert peak >= 2
    finally:
        run_ids = [run_id]
        async with _conn() as conn:
            run_ids.extend(str(row["child_run_id"]) for row in await _delegations(conn, run_id))
        await _cleanup(run_ids, [graph_id])


@pytest.mark.asyncio
async def test_duplicate_delegation_is_idempotent(real_postgres_dsn: str):
    run_id = str(uuid4())
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    token_id = uuid5(ORCHESTRATION_NAMESPACE, f"root-token:{TENANT}:{run_id}")
    principal = Principal(
        tenant_id=TENANT, user_id=USER, scopes=["agent:execute"], permissions=["agent:execute"],
        authentication_method="dev", session_id="subagent-session",
    )
    try:
        root = await CapabilityTokenService.issue_root(
            principal, run_id, token_id=token_id, ttl_seconds=600,
        )
        payload = {
            "tenant_id": TENANT,
            "parent_run_id": run_id,
            "principal": principal.model_dump(mode="json"),
            "root_token_id": str(token_id),
            "root_expires_at": root.expires_at.isoformat(),
            "root_goal": "idempotency",
            "graph_id": None,
            "node": {"node_id": "dup", "name": "dup", "description": "dup", "agent_role": "executor", "timeout_seconds": 60},
            "attempt": 1,
            "upstream": [],
            "max_depth": 3,
            "max_steps": 4,
            "max_duration_seconds": 300,
            "parent_agent_id": "parent",
        }
        first = await create_delegation_activity(payload)
        second = await create_delegation_activity(payload)
        assert first["created"] is True
        assert second["created"] is False
        assert first["delegation"]["updated_at"] is not None
        assert second["delegation"]["updated_at"] is not None
        assert first["child_workflow_id"] == second["child_workflow_id"]
        assert first["child_input"]["run_id"] == second["child_input"]["run_id"]
    finally:
        child_id = deterministic_child_run_id(TENANT, run_id, "dup", 1)
        await _cleanup([run_id, str(child_id)], [])


@pytest.mark.asyncio
async def test_subagent_failure_policy(temporal_client, worker, monkeypatch):
    run_id, graph_id = str(uuid4()), f"sub-fail-{uuid4()}"
    failing_child = deterministic_child_run_id(TENANT, run_id, "child-fail", 1)
    monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "0.1")
    monkeypatch.setenv("GRAPH_IT_FAIL_RUNS", str(failing_child))
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    proposal = _proposal(["child-fail", "child-ok", "final-2"], [
        ("child-fail", "final-2"), ("child-ok", "final-2"),
    ])
    try:
        handle = await temporal_client.start_workflow(
            AgentRunWorkflow.run, _input(run_id, graph_id, proposal),
            id=f"subagent-fail-{run_id}", task_queue=TASK_QUEUE,
            execution_timeout=timedelta(seconds=TIMEOUT),
        )
        result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
        assert result["status"] == "FAILED"
        async with _conn() as conn:
            rows = {row["node_id"]: row for row in await _delegations(conn, run_id)}
            nodes = await _node_rows(conn, graph_id)
        assert rows["child-fail"]["status"] == "FAILED"
        assert rows["child-ok"]["status"] == "COMPLETED"
        assert nodes["child-fail"]["status"] == "FAILED"
        assert nodes["child-ok"]["status"] == "COMPLETED"
        assert nodes["final-2"]["status"] == "SKIPPED"
    finally:
        run_ids = [run_id]
        async with _conn() as conn:
            run_ids.extend(str(row["child_run_id"]) for row in await _delegations(conn, run_id))
        await _cleanup(run_ids, [graph_id])


@pytest.mark.asyncio
async def test_subagent_worker_restart_recovery(temporal_client, monkeypatch):
    monkeypatch.setenv("GRAPH_IT_SLEEP_SECONDS", "1.2")
    run_id, graph_id = str(uuid4()), f"sub-restart-{uuid4()}"
    async with _conn() as conn:
        await _seed_parent(conn, run_id)
    proposal = _proposal(["child-restart", "final-3"], [("child-restart", "final-3")])
    started_worker = await _worker(temporal_client, TASK_QUEUE_RESTART)
    started_task = asyncio.create_task(started_worker.run())
    await asyncio.sleep(0.5)
    try:
        handle = await temporal_client.start_workflow(
            AgentRunWorkflow.run, _input(run_id, graph_id, proposal),
            id=f"subagent-restart-{run_id}", task_queue=TASK_QUEUE_RESTART,
            execution_timeout=timedelta(seconds=TIMEOUT),
        )
        for _ in range(200):
            async with _conn() as conn:
                rows = await _delegations(conn, run_id)
            if rows and rows[0]["status"] in {"RUNNING", "COMPLETED"}:
                break
            await asyncio.sleep(0.1)
        assert rows and rows[0]["status"] in {"RUNNING", "COMPLETED"}
        await started_worker.shutdown()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(started_task, timeout=10)
        if not started_task.done():
            started_task.cancel()
            with suppress(asyncio.CancelledError):
                await started_task

        replacement = await _worker(temporal_client, TASK_QUEUE_RESTART)
        replacement_task = asyncio.create_task(replacement.run())
        await asyncio.sleep(0.5)
        try:
            result = await asyncio.wait_for(handle.result(), timeout=TIMEOUT)
            assert result["status"] == "SUCCEEDED"
            async with _conn() as conn:
                rows = await _delegations(conn, run_id)
                nodes = await _node_rows(conn, graph_id)
            assert rows[0]["status"] == "COMPLETED"
            assert nodes["child-restart"]["status"] == "COMPLETED"
            assert nodes["final-3"]["status"] == "COMPLETED"
        finally:
            await replacement.shutdown()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(replacement_task, timeout=10)
            if not replacement_task.done():
                replacement_task.cancel()
                with suppress(asyncio.CancelledError):
                    await replacement_task
    finally:
        run_ids = [run_id]
        async with _conn() as conn:
            run_ids.extend(str(row["child_run_id"]) for row in await _delegations(conn, run_id))
        await _cleanup(run_ids, [graph_id])
