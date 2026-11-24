"""Module 3.3.4：Child Context 隔离、投影与 Artifact Ownership 测试。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from agent_runtime.activities_context import _persistence as persistence
from agent_runtime.context.assembly import pack_to_model_payload
from agent_runtime.context.pack import ContextItem, ContextMetadata, ContextPack, GoalContext, Provenance
from agent_runtime.contracts import WorkflowInput
from agent_runtime.db import session_for_tenant
from agent_runtime.graph.models import GraphDefinition, NodeResult
from agent_runtime.graph.runner import _node_base_state
from agent_runtime.orchestration.context_projection import ChildContextInput, project_child_context

TENANT = "child-context-tenant"


def _parent_pack(pack_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "metadata": {"context_pack_id": pack_id, "tenant_id": TENANT, "run_id": "parent-run"},
        "goal": {
            "original_user_goal": {"value": "parent goal"},
            "acceptance_criteria": [{"value": "criterion"}],
            "immutable_constraints": [{"value": "constraint"}],
        },
        "historical": {
            "rolling_summary": {"value": "parent summary"},
            "recent_events": [{"value": "PARENT_SECRET_SENTINEL"}],
        },
    }


def _input(**overrides):
    payload = dict(
        tenant_id=TENANT,
        parent_run_id="parent-run",
        child_run_id="child-run",
        delegation_id="delegation-1",
        capability_token_id="child-token",
        agent_role="researcher",
        task_instruction="do child task",
        task_parameters={"node": {"nested": {"value": 1}}},
        context_references=[{
            "context_pack_id": "pack-1", "run_id": "parent-run",
            "tenant_id": TENANT, "include_summary": True,
        }],
        upstream_nodes=[{"node_id": "a", "status": "COMPLETED", "output_summary": {"nested": {"value": 1}}}],
        artifact_refs=[{"artifact_id": "art-allowed"}, {"artifact_id": "art-denied"}],
        immutable_constraints=["do not invent data"],
    )
    payload.update(overrides)
    return ChildContextInput.model_validate(payload)


def test_projection_includes_only_authorized_summary_and_deep_copies():
    input_data = _input()
    seed = project_child_context(
        input_data,
        parent_pack=_parent_pack("pack-1"),
        allowed_artifacts={
            "art-allowed": {"artifact_id": "art-allowed", "tenant_id": TENANT, "run_id": "parent-run", "object_ref": "ref", "summary": "ok"},
        },
    )
    assert seed.parent_summary["original_user_goal"] == "parent goal"
    assert seed.parent_summary["rolling_summary"] == "parent summary"
    assert "PARENT_SECRET_SENTINEL" not in json.dumps(seed.model_dump(), ensure_ascii=False)
    assert [item["node_id"] for item in seed.upstream_nodes] == ["a"]
    assert [item["artifact_id"] for item in seed.artifact_refs] == ["art-allowed"]
    assert {item.reason for item in seed.rejected_references} == {"artifact_not_allowed"}

    seed.upstream_nodes[0]["output_summary"]["nested"]["value"] = 99
    seed.task_parameters["node"]["nested"]["value"] = 99
    assert input_data.upstream_nodes[0]["output_summary"]["nested"]["value"] == 1
    assert input_data.task_parameters["node"]["nested"]["value"] == 1


def test_projection_rejects_cross_tenant_run_and_artifact():
    input_data = _input(
        context_references=[
            {"context_pack_id": "pack-x", "run_id": "parent-run", "tenant_id": "other", "include_summary": True},
            {"context_pack_id": "pack-y", "run_id": "other-run", "tenant_id": TENANT, "include_summary": True},
            {"context_pack_id": "pack-1", "run_id": "parent-run", "tenant_id": TENANT, "include_summary": True},
        ],
        artifact_refs=[{"artifact_id": "art-foreign"}],
    )
    seed = project_child_context(
        input_data,
        parent_pack=_parent_pack("pack-1"),
        allowed_artifacts={
            "art-foreign": {"artifact_id": "art-foreign", "tenant_id": "other", "run_id": "parent-run", "object_ref": "ref", "summary": "x"},
        },
    )
    reasons = {(item.kind, item.reason) for item in seed.rejected_references}
    assert ("context_pack", "cross_tenant_reference") in reasons
    assert ("context_pack", "run_ownership_mismatch") in reasons
    assert ("artifact", "cross_tenant_artifact") in reasons
    assert seed.source_context_pack_ids == ["pack-1"]


def test_context_pack_ownership_fields_and_model_payload_isolation():
    metadata = ContextMetadata(
        run_id="child-run", tenant_id=TENANT, parent_run_id="parent-run",
        delegation_id="delegation-1", capability_token_id="child-token",
        context_source="child_projection", source_context_pack_ids=["pack-1"],
    )
    pack = ContextPack(metadata=metadata, goal=GoalContext(
        original_user_goal=ContextItem(
            value="child goal", provenance=Provenance(source_type="system_instruction"),
        ),
    ))
    pack.execution.upstream_node_outputs = [{"node_id": "a", "output": {"nested": {"value": 1}}}]
    payload = pack_to_model_payload(pack, [], [])
    assert payload["upstream_node_outputs"][0]["output"]["nested"]["value"] == 1
    payload["upstream_node_outputs"][0]["output"]["nested"]["value"] = 99
    assert pack.execution.upstream_node_outputs[0]["output"]["nested"]["value"] == 1
    restored = ContextPack.model_validate(json.loads(pack.model_dump_json()))
    assert restored.metadata.parent_run_id == "parent-run"
    assert restored.metadata.context_source == "child_projection"


def test_graph_node_state_does_not_share_result_objects():
    definition = GraphDefinition(
        graph_id="ctx-graph", run_id="run", tenant_id=TENANT,
        root_goal="goal", nodes=[
            {"node_id": "a", "name": "A", "description": "a"},
            {"node_id": "b", "name": "B", "description": "b", "dependencies": ["a"]},
        ],
        edges=[{"source_node_id": "a", "target_node_id": "b"}],
    )
    result = NodeResult(node_id="a", graph_id="ctx-graph", status="COMPLETED", output={"nested": {"value": 1}})
    run_input = WorkflowInput(
        run_id="run", tenant_id=TENANT, user_id="u", session_id="s", agent_id="agent",
        prompt="goal", scopes=[], max_steps=5, max_duration_seconds=60,
    )
    state = {"principal": {}, "memory_context": [], "skills": [], "workflow_input": run_input.model_dump(mode="json")}
    node_state = _node_base_state(run_input, state, definition, definition.node_map["b"], {"a": result})
    node_state["context_model_payload"]["upstream_nodes"][0]["output"]["nested"]["value"] = 99
    assert result.output["nested"]["value"] == 1


def test_tool_activities_receive_capability_token_from_workflow_state():
    root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    workflow_source = (root / "workflow.py").read_text(encoding="utf-8")
    graph_source = (root / "graph" / "runner.py").read_text(encoding="utf-8")
    assert '"capability_token_id": state.get("capability_token_id")' in workflow_source
    assert '"capability_token_id": capability_token_id' in graph_source


@pytest.mark.asyncio
async def test_context_and_artifact_ownership_is_tenant_and_run_scoped(real_postgres_dsn: str):
    parent_run, child_a, child_b = uuid4(), uuid4(), uuid4()
    artifact_id = str(uuid4())
    conn = await asyncpg.connect(real_postgres_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
            for run_id in (parent_run, child_a, child_b):
                await conn.execute(
                    """INSERT INTO agent_runs (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
                       VALUES ($1::uuid, $2, 'u', 's', 'a', 'p', 'RUNNING', 'ROOT', '{}'::json)
                       ON CONFLICT (run_id) DO NOTHING""",
                    run_id, TENANT,
                )
            await conn.execute(
                """INSERT INTO context_artifacts
                   (artifact_id, tenant_id, run_id, object_ref, summary, acl, size_bytes)
                   VALUES ($1::uuid, $2, $3::uuid, 'child-contexts/art', 'child artifact', '[]'::json, 10)
                   ON CONFLICT (artifact_id) DO NOTHING""",
                artifact_id, TENANT, child_a,
            )
    finally:
        await conn.close()

    principal = {
        "tenant_id": TENANT,
        "scopes": {"artifact:read:tenant"},
        "permissions": set(),
    }
    try:
        async with session_for_tenant(TENANT) as session:
            allowed = await persistence.get_artifact(
                session, tenant_id=TENANT, artifact_id=artifact_id,
                principal_tenant_id=principal["tenant_id"],
                principal_scopes=principal["scopes"],
                allowed_run_ids={str(child_a)},
            )
            denied = await persistence.get_artifact(
                session, tenant_id=TENANT, artifact_id=artifact_id,
                principal_tenant_id=principal["tenant_id"],
                principal_scopes=principal["scopes"],
                allowed_run_ids={str(child_b)},
            )
        assert allowed is not None and allowed.artifact_id == artifact_id
        assert denied is None
    finally:
        conn = await asyncpg.connect(real_postgres_dsn.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT)
                await conn.execute("DELETE FROM context_artifacts WHERE artifact_id = $1::uuid", artifact_id)
                await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", [parent_run, child_a, child_b])
        finally:
            await conn.close()
