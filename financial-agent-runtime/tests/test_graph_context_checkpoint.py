"""Task Graph 与 ContextPack / Checkpoint 的集成测试。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from agent_runtime.checkpoint.builder import build_envelope_fields, build_state_snapshot
from agent_runtime.checkpoint.models import CHECKPOINT_SCHEMA_VERSION, CheckpointEnvelope
from agent_runtime.context.pack import ContextMetadata
from agent_runtime.graph.models import GraphDefinition, NodeResult
from agent_runtime.graph.runner import _node_base_state, _sync_state


def _definition():
    return GraphDefinition(
        graph_id="ctx-graph", run_id="ctx-run", tenant_id="ctx-tenant",
        root_goal="汇总风险信息", acceptance_criteria=["说明数据缺口"],
        nodes=[
            {"node_id": "a", "name": "A", "description": "交易查询"},
            {"node_id": "b", "name": "B", "description": "KYC 分析"},
            {"node_id": "e", "name": "E", "node_type": "aggregation",
             "description": "汇总报告", "dependencies": ["a", "b"]},
        ],
        edges=[
            {"source_node_id": "a", "target_node_id": "e"},
            {"source_node_id": "b", "target_node_id": "e"},
        ],
    )


def _result(node_id: str, output: dict, status: str = "COMPLETED"):
    return NodeResult(
        node_id=node_id, graph_id="ctx-graph", tenant_id="ctx-tenant",
        run_id="ctx-run", status=status, output=output,
        artifact_refs=[{"artifact_id": f"art-{node_id}"}] if node_id == "a" else [],
        source_event_ids=[f"evt-{node_id}"],
    )


def _run_input():
    class RunInput:
        run_id = "ctx-run"
        tenant_id = "ctx-tenant"
        user_id = "ctx-user"
        session_id = "ctx-session"
        agent_id = "ctx-agent"
        prompt = "汇总风险信息"
        model_name = "model-x"
        harness_version = "h1"
        context_config = {"immutable_constraints": ["不得编造结论"]}

    return RunInput()


def test_node_context_is_scoped_to_declared_dependencies():
    definition = _definition()
    upstream = {"a": _result("a", {"summary": "交易结果"}), "b": _result("b", {"summary": "KYC 结果"})}
    state = {"principal": {"user_id": "ctx-user"}, "memory_context": [], "skills": []}
    node_state = _node_base_state(_run_input(), state, definition, definition.node_map["b"], upstream)
    payload = node_state["context_model_payload"]
    assert payload["root_goal"] == definition.root_goal
    assert [item["node_id"] for item in payload["upstream_nodes"]] == []
    assert "交易结果" not in json.dumps(payload, ensure_ascii=False)


def test_aggregation_context_includes_sources_and_gaps():
    definition = _definition()
    upstream = {
        "a": _result("a", {"summary": "交易结果"}),
        "b": _result("b", {}, status="FAILED"),
    }
    state = {"principal": {}, "memory_context": [], "skills": []}
    node_state = _node_base_state(_run_input(), state, definition, definition.node_map["e"], upstream)
    payload = node_state["context_model_payload"]
    assert payload["schema_version"] == "graph-aggregation-v1"
    sources = {item["node_id"]: item for item in payload["upstream_nodes"]}
    assert sources["a"]["artifact_refs"] == [{"artifact_id": "art-a"}]
    assert sources["a"]["source_event_ids"] == ["evt-a"]
    assert sources["b"]["status"] == "FAILED"
    assert "数据缺口" in payload["instruction"]


def test_context_metadata_accepts_graph_node_scope():
    metadata = ContextMetadata(
        run_id="ctx-run", tenant_id="ctx-tenant", graph_id="ctx-graph",
        graph_version=2, node_id="e", parent_node_ids=["a", "b"],
        node_type="aggregation", context_policy="summary_only",
    )
    dumped = metadata.model_dump(mode="json")
    assert dumped["graph_id"] == "ctx-graph"
    assert dumped["parent_node_ids"] == ["a", "b"]
    restored = ContextMetadata.model_validate(dumped)
    assert restored.node_id == "e"


def test_checkpoint_graph_state_reference_round_trip():
    definition = _definition()
    states = {"a": "COMPLETED", "b": "RUNNING", "e": "PENDING"}
    blocked = {"e": ["b"]}
    results = {"a": _result("a", {"summary": "ok"})}
    state = {"graph": {}, "history": []}
    _sync_state(state, definition, states, blocked, results, 9)
    assert state["graph"]["graph_event_cursor"] == 9
    assert state["graph"]["active_node_ids"] == ["b"]
    assert state["graph"]["completed_nodes"] == ["a"]
    assert isinstance(state["completed_steps"][0], dict)

    envelope = CheckpointEnvelope(
        checkpoint_id=str(uuid4()), tenant_id="ctx-tenant", run_id="ctx-run",
        checkpoint_sequence=1, created_at=datetime.now(timezone.utc),
        state_digest="digest", blob_sha256="sha", blob_size_bytes=10,
        state_blob_ref="ref", graph_state_ref=state["graph"],
    )
    payload = json.loads(envelope.model_dump_json())
    assert payload["graph_state_ref"]["node_states"] == states
    assert CheckpointEnvelope.model_validate(payload).graph_state_ref["graph_version"] == definition.graph_version


def test_legacy_checkpoint_v2_without_graph_ref_still_reads():
    legacy = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": str(uuid4()),
        "tenant_id": "ctx-tenant",
        "run_id": "ctx-run",
        "checkpoint_sequence": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state_digest": "digest",
        "blob_sha256": "sha",
        "blob_size_bytes": 10,
        "state_blob_ref": "ref",
    }
    envelope = CheckpointEnvelope.model_validate(legacy)
    assert envelope.graph_state_ref is None
    assert envelope.schema_version == CHECKPOINT_SCHEMA_VERSION


def test_build_envelope_fields_includes_graph_state():
    definition = _definition()
    states = {"a": "COMPLETED", "b": "PENDING", "e": "PENDING"}
    state = {
        "graph": {}, "history": [], "task_graph_version": "v3",
        "graph_state_ref": {"graph_id": definition.graph_id, "node_states": states},
    }
    _sync_state(state, definition, states, {}, {}, 1)
    snapshot = build_state_snapshot(state)
    fields = build_envelope_fields(
        "cp-1", "ctx-tenant", "ctx-run", 1, state, snapshot,
        temporal_workflow_id="wf", temporal_execution_id="run",
    )
    assert fields["task_graph_version"] == "v1"
    assert fields["graph_state_ref"]["node_states"] == states
