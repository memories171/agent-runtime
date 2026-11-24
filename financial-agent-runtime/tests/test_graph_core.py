"""Task Graph Engine 核心单测（纯确定性，无需数据库）。"""
from __future__ import annotations

import pytest

from agent_runtime.graph.models import (
    GraphDefinition, GraphLimits, GraphProposal, NodeResult, ValidationIssue,
)
from agent_runtime.graph.policies import (
    ErrorClass, backoff_seconds, classify_error, failure_policy_action, should_retry,
)
from agent_runtime.graph.scheduler import (
    build_aggregation_context, evaluate_dependencies, select_ready, validate_node_output,
)
from agent_runtime.graph.state_machine import (
    InvalidTransitionError, all_terminal, apply_node_status, assert_node_transition,
)
from agent_runtime.graph.validate import build_definition, validate_proposal


def _proposal(nodes=None, edges=None, **kwargs):
    defaults = dict(
        root_goal="测试目标",
        nodes=nodes if nodes is not None else [
            {"node_id": "a", "name": "A", "description": "task a"},
            {"node_id": "b", "name": "B", "description": "task b", "dependencies": ["a"]},
        ],
        edges=edges if edges is not None else [
            {"source_node_id": "a", "target_node_id": "b", "dependency_type": "SUCCESS"},
        ],
    )
    defaults.update(kwargs)
    return GraphProposal.model_validate(defaults)


def _validate(proposal, *, limits=None, allowed=None):
    return validate_proposal(
        proposal,
        limits=limits or GraphLimits(),
        allowed_permissions=allowed or set(),
        graph_id="g-test",
        run_id="r-test",
        tenant_id="t-test",
    )


class TestValidation:
    def test_valid_graph(self):
        report = _validate(_proposal())
        assert report.valid, report.issues
        assert report.execution_order == ["a", "b"]
        assert report.max_depth == 2

    def test_duplicate_node_id(self):
        proposal = _proposal(nodes=[
            {"node_id": "a", "name": "A"}, {"node_id": "a", "name": "A2"},
        ], edges=[])
        report = _validate(proposal)
        assert any(issue.code == "DUPLICATE_NODE_ID" for issue in report.issues)

    def test_cycle(self):
        proposal = _proposal(edges=[
            {"source_node_id": "a", "target_node_id": "b"},
            {"source_node_id": "b", "target_node_id": "a"},
        ])
        report = _validate(proposal)
        assert any(issue.code == "CYCLE_DETECTED" for issue in report.issues)

    def test_dangling_edge_and_independent_nodes(self):
        proposal = _proposal(nodes=[
            {"node_id": "a", "name": "A"},
            {"node_id": "b", "name": "B"},
            {"node_id": "c", "name": "C"},
        ], edges=[{"source_node_id": "a", "target_node_id": "missing"}])
        report = _validate(proposal)
        codes = {issue.code for issue in report.issues}
        assert "DANGLING_EDGE" in codes
        assert "ORPHAN_NODE" not in codes, "independent nodes are valid entries/terminals"

    def test_permission_and_high_risk(self):
        proposal = _proposal(nodes=[
            {"node_id": "a", "name": "A", "required_permissions": ["secret:write"], "risk_level": "high"},
            {"node_id": "b", "name": "B", "dependencies": ["a"]},
        ])
        report = _validate(proposal, allowed=set())
        codes = {issue.code for issue in report.issues}
        assert "PERMISSION_NOT_ALLOWED" in codes
        assert "HIGH_RISK_NOT_ALLOWED" in codes

    def test_limits(self):
        nodes = [{"node_id": f"n{i}", "name": "N"} for i in range(4)]
        proposal = _proposal(nodes=nodes, edges=[
            {"source_node_id": f"n{i}", "target_node_id": f"n{i+1}"} for i in range(3)
        ])
        report = _validate(proposal, limits=GraphLimits(max_nodes=3, max_graph_depth=2))
        codes = {issue.code for issue in report.issues}
        assert "TOO_MANY_NODES" in codes
        assert "GRAPH_TOO_DEEP" in codes

    def test_dynamic_condition_rejected(self):
        proposal = _proposal(edges=[{
            "source_node_id": "a", "target_node_id": "b", "condition": {"expr": "x>1"},
        }])
        report = _validate(proposal)
        assert any(issue.code == "CONDITION_UNSUPPORTED" for issue in report.issues)

    def test_build_definition_normalizes_edges_and_dependencies(self):
        proposal = _proposal()
        report = _validate(proposal)
        definition = build_definition(proposal, report, graph_id="g-test", run_id="r-test", tenant_id="t-test")
        assert definition.graph_status == "READY"
        assert definition.edges[0].edge_id == "e-1"
        assert definition.node_map["b"].dependencies == ["a"]
        assert definition.node_map["b"].execution_order == 1


class TestStateMachine:
    def test_legal_and_illegal_transitions(self):
        assert_node_transition("PENDING", "READY")
        assert_node_transition("RUNNING", "WAITING_APPROVAL")
        assert_node_transition("WAITING_APPROVAL", "RUNNING")
        with pytest.raises(InvalidTransitionError):
            assert_node_transition("COMPLETED", "RUNNING")
        with pytest.raises(InvalidTransitionError):
            assert_node_transition("PENDING", "COMPLETED")

    def test_apply_node_status(self):
        states = {"a": "PENDING", "b": "RUNNING"}
        apply_node_status(states, "a", "READY")
        apply_node_status(states, "a", "RUNNING")
        apply_node_status(states, "a", "RETRY_WAIT")
        apply_node_status(states, "a", "READY")
        apply_node_status(states, "a", "RUNNING")
        apply_node_status(states, "a", "COMPLETED")
        assert states["a"] == "COMPLETED"
        assert not all_terminal(states)


class TestScheduler:
    def _definition(self, policy="CONTINUE_INDEPENDENT"):
        proposal = _proposal(failure_policy=policy)
        report = _validate(proposal)
        return build_definition(proposal, report, graph_id="g-test", run_id="r-test", tenant_id="t-test")

    def test_ready_blocks_dependent(self):
        definition = self._definition()
        evaluation = evaluate_dependencies(definition, {"a": "PENDING", "b": "PENDING"})
        assert evaluation.ready == ["a"]
        assert evaluation.blocked == {"b": ["a"]}
        assert select_ready(definition, evaluation, 1) == ["a"]

    def test_success_unlocks_dependent(self):
        definition = self._definition()
        evaluation = evaluate_dependencies(definition, {"a": "COMPLETED", "b": "PENDING"})
        assert evaluation.ready == ["b"]

    def test_continue_independent_skips_dependent(self):
        definition = self._definition()
        evaluation = evaluate_dependencies(definition, {"a": "FAILED", "b": "PENDING"})
        assert evaluation.skipped == ["b"]
        assert evaluation.ready == []

    def test_fail_fast_cancels_all(self):
        definition = self._definition("FAIL_FAST")
        evaluation = evaluate_dependencies(definition, {"a": "FAILED", "b": "PENDING"})
        assert evaluation.stop_all is True
        assert evaluation.cancelled == ["b"]

    def test_manual_intervention_waits(self):
        definition = self._definition("MANUAL_INTERVENTION")
        evaluation = evaluate_dependencies(definition, {"a": "FAILED", "b": "PENDING"})
        assert evaluation.wait_manual is True
        assert evaluation.ready == []

    def test_aggregation_marks_data_gaps(self):
        definition = self._definition()
        results = {
            "a": NodeResult(node_id="a", graph_id="g-test", status="COMPLETED", output={"summary": "ok"}),
            "b": NodeResult(node_id="b", graph_id="g-test", status="FAILED", error="boom"),
        }
        context = build_aggregation_context(definition, results)
        assert context["data_gaps"] == ["b"]
        assert context["missing_nodes"] == []
        assert context["upstream_nodes"][0]["status"] == "COMPLETED"


class TestPolicies:
    def test_error_classification(self):
        assert classify_error(PermissionError("permission_denied")) == ErrorClass.NON_RETRYABLE
        assert classify_error(ValueError("schema_error: bad output")) == ErrorClass.NON_RETRYABLE
        assert classify_error(TimeoutError("gateway timeout")) == ErrorClass.RETRYABLE

    def test_retry_and_backoff(self):
        assert should_retry(ErrorClass.RETRYABLE, 1, 3) is True
        assert should_retry(ErrorClass.RETRYABLE, 3, 3) is False
        assert should_retry(ErrorClass.NON_RETRYABLE, 1, 3) is False
        assert backoff_seconds(1, 2.0, 2.0, 30.0) == 2.0
        assert backoff_seconds(5, 2.0, 2.0, 30.0) == 30.0

    def test_failure_policy_action(self):
        assert failure_policy_action("FAIL_FAST", True) == "stop_all"
        assert failure_policy_action("MANUAL_INTERVENTION", True) == "wait_manual"
        assert failure_policy_action("CONTINUE_INDEPENDENT", True) == "skip_dependents"
        assert failure_policy_action("FAIL_FAST", False) == "continue"


class TestOutputSchema:
    def test_required_and_types(self):
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
        assert validate_node_output({"summary": "ok"}, schema) == (True, None)
        ok, reason = validate_node_output({"summary": 1}, schema)
        assert ok is False and "summary" in reason
        ok, reason = validate_node_output({}, schema)
        assert ok is False and "missing" in reason
