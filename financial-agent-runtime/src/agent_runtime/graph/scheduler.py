"""DAG 调度计算：纯函数，供 Workflow 内确定性调用。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.graph.models import DependencyType, GraphDefinition, NodeResult, NodeStatus
from agent_runtime.graph.policies import failure_policy_action
from agent_runtime.graph.state_machine import terminal


@dataclass
class DependencyEvaluation:
    ready: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    stop_all: bool = False
    wait_manual: bool = False


def _satisfied(source_status: NodeStatus, dependency_type: DependencyType) -> bool:
    if dependency_type == "SUCCESS":
        return source_status == "COMPLETED"
    if dependency_type == "FAILURE":
        return source_status == "FAILED"
    return terminal(source_status)


def _broken(source_status: NodeStatus, dependency_type: DependencyType) -> bool:
    if dependency_type == "FAILURE":
        return False
    return source_status in {"FAILED", "CANCELLED", "TIMED_OUT", "SKIPPED"}


def evaluate_dependencies(
    definition: GraphDefinition,
    states: dict[str, NodeStatus],
) -> DependencyEvaluation:
    """根据当前节点状态与 Failure Policy 计算下一轮可执行节点。"""
    evaluation = DependencyEvaluation()
    node_map = definition.node_map
    has_failed = any(status in {"FAILED", "TIMED_OUT"} for status in states.values())

    if has_failed and definition.failure_policy == "FAIL_FAST":
        evaluation.stop_all = True
        for node in definition.nodes:
            if not terminal(states.get(node.node_id, "PENDING")) and states.get(node.node_id) != "FAILED":
                evaluation.cancelled.append(node.node_id)
        return evaluation

    incoming: dict[str, list[Any]] = {node.node_id: [] for node in definition.nodes}
    for edge in definition.edges:
        if edge.target_node_id in incoming:
            incoming[edge.target_node_id].append(edge)

    for node in definition.nodes:
        nid = node.node_id
        status = states.get(nid, "PENDING")
        if terminal(status):
            continue
        unsatisfied: list[str] = []
        broken = False
        for edge in sorted(incoming[nid], key=lambda item: item.source_node_id):
            source_status = states.get(edge.source_node_id, "PENDING")
            if _broken(source_status, edge.dependency_type):
                broken = True
                continue
            if not _satisfied(source_status, edge.dependency_type):
                unsatisfied.append(edge.source_node_id)
        if broken:
            action = failure_policy_action(definition.failure_policy, has_failed_dependency=True)
            if action == "wait_manual":
                evaluation.wait_manual = True
                evaluation.blocked[nid] = unsatisfied
            else:
                evaluation.skipped.append(nid)
        elif unsatisfied:
            evaluation.blocked[nid] = unsatisfied
        else:
            evaluation.ready.append(nid)

    order = {node.node_id: (node.execution_order, node.node_id) for node in definition.nodes}
    evaluation.ready.sort(key=lambda nid: order[nid])
    evaluation.skipped.sort(key=lambda nid: order[nid])
    evaluation.cancelled.sort(key=lambda nid: order[nid])
    return evaluation


def select_ready(definition: GraphDefinition, evaluation: DependencyEvaluation, max_concurrency: int) -> list[str]:
    return evaluation.ready[:max(1, max_concurrency)]


def build_aggregation_context(
    definition: GraphDefinition,
    results: dict[str, NodeResult],
    *,
    inline_char_limit: int = 2_000,
) -> dict[str, Any]:
    """汇聚节点上下文：明确列出缺失/失败节点，禁止伪装为成功。"""
    upstream: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for node in definition.nodes:
        result = results.get(node.node_id)
        if result is None:
            missing.append({"node_id": node.node_id, "name": node.name, "reason": "NO_RESULT"})
            continue
        raw = json.dumps(result.output or {}, ensure_ascii=False, default=str)
        item = {
            "node_id": node.node_id,
            "name": node.name,
            "status": result.status,
            "output_summary": raw[:inline_char_limit],
            "truncated": len(raw) > inline_char_limit,
            "artifact_refs": result.artifact_refs,
            "source_event_ids": result.source_event_ids,
            "error": result.error,
            "attempts": result.attempts,
            "execution_duration_ms": result.duration_ms,
            "token_usage": {"input": result.token_input, "output": result.token_output},
        }
        upstream.append(item)
    failed = [item for item in upstream if item["status"] != "COMPLETED"]
    return {
        "schema_version": "graph-aggregation-v1",
        "root_goal": definition.root_goal,
        "acceptance_criteria": definition.acceptance_criteria,
        "upstream_nodes": upstream,
        "failed_or_skipped_nodes": failed,
        "missing_nodes": missing,
        "data_gaps": [item["node_id"] for item in failed + missing],
        "instruction": (
            "汇总时只能引用 upstream_nodes 中 status=COMPLETED 的结果。"
            "data_gaps 中列出的节点必须作为数据缺口显式说明，不得推断其结论。"
        ),
    }


def validate_node_output(output: dict[str, Any] | None, schema: dict[str, Any]) -> tuple[bool, str | None]:
    """第一阶段只校验顶层必填字段与类型，避免引入完整 JSON Schema 依赖。"""
    if not schema:
        return True, None
    if output is None:
        return False, "output is null"
    if not isinstance(output, dict):
        return False, "output must be an object"
    required = schema.get("required") or []
    missing = [key for key in required if key not in output]
    if missing:
        return False, f"missing required fields: {missing}"
    properties = schema.get("properties") or {}
    for key, spec in properties.items():
        if key not in output or not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        value = output[key]
        if expected == "string" and not isinstance(value, str):
            return False, f"field {key} must be string"
        if expected == "number" and not isinstance(value, (int, float)):
            return False, f"field {key} must be number"
        if expected == "boolean" and not isinstance(value, bool):
            return False, f"field {key} must be boolean"
        if expected == "array" and not isinstance(value, list):
            return False, f"field {key} must be array"
        if expected == "object" and not isinstance(value, dict):
            return False, f"field {key} must be object"
    return True, None
