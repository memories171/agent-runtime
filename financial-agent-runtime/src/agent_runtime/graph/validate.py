"""DAG / Schema / Policy 校验：纯确定性，不信任 LLM 提案。"""
from __future__ import annotations

from collections import deque
from typing import Any

from agent_runtime.graph.models import (
    GraphDefinition, GraphLimits, GraphProposal, ValidationIssue, ValidationReport,
)

_ALLOWED_TOP_KEYS = {"type", "properties", "required", "items", "enum", "description"}


def _validate_schema(schema: dict[str, Any], label: str, issues: list[ValidationIssue]) -> None:
    if not isinstance(schema, dict):
        issues.append(ValidationIssue(code="INVALID_SCHEMA", message=f"{label} must be an object"))
        return
    if schema and schema.get("type") not in {None, "object", "array"}:
        issues.append(ValidationIssue(code="INVALID_SCHEMA", message=f"{label}.type must be object/array"))
    unknown = set(schema) - _ALLOWED_TOP_KEYS
    if unknown:
        issues.append(ValidationIssue(code="UNSUPPORTED_SCHEMA_KEY", message=f"{label} has unsupported keys: {sorted(unknown)}"))


def topological_order(node_ids: list[str], edges: list[Any]) -> tuple[list[str], bool, int]:
    """Kahn 拓扑排序。返回 (顺序, 是否有环, 最大深度)。"""
    indegree = {nid: 0 for nid in node_ids}
    outgoing: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        if edge.source_node_id in indegree and edge.target_node_id in indegree:
            indegree[edge.target_node_id] += 1
            outgoing[edge.source_node_id].append(edge.target_node_id)
    queue = deque(sorted(nid for nid, degree in indegree.items() if degree == 0))
    order: list[str] = []
    depth = {nid: 1 for nid in node_ids}
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for target in sorted(outgoing[nid]):
            depth[target] = max(depth[target], depth[nid] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order, len(order) != len(node_ids), max(depth.values(), default=0)


def validate_proposal(
    proposal: GraphProposal,
    *,
    limits: GraphLimits,
    allowed_permissions: set[str],
    graph_id: str,
    run_id: str,
    tenant_id: str,
    graph_version: int = 1,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    nodes = proposal.nodes
    node_ids = [node.node_id for node in nodes]

    if not nodes:
        issues.append(ValidationIssue(code="NO_NODES", message="graph must contain at least one node"))
    if len(nodes) > limits.max_nodes:
        issues.append(ValidationIssue(code="TOO_MANY_NODES", message=f"node count {len(nodes)} > {limits.max_nodes}"))
    if len(set(node_ids)) != len(node_ids):
        issues.append(ValidationIssue(code="DUPLICATE_NODE_ID", message="node_id must be unique"))
    if proposal.max_concurrency > limits.max_concurrency:
        issues.append(ValidationIssue(
            code="CONCURRENCY_LIMIT", message=f"max_concurrency {proposal.max_concurrency} > {limits.max_concurrency}",
        ))

    for node in nodes:
        missing = sorted(set(node.required_permissions) - allowed_permissions)
        if missing:
            issues.append(ValidationIssue(
                code="PERMISSION_NOT_ALLOWED",
                message=f"node {node.node_id} requires ungranted permissions: {missing}",
                node_id=node.node_id,
            ))
        if node.risk_level == "high" and node.node_id not in limits.allowed_high_risk_nodes:
            issues.append(ValidationIssue(
                code="HIGH_RISK_NOT_ALLOWED",
                message=f"node {node.node_id} is high risk and not allowlisted",
                node_id=node.node_id,
            ))
        if node.node_type == "sub_agent":
            role = node.agent_role or "executor"
            if role not in limits.allowed_agent_roles:
                issues.append(ValidationIssue(
                    code="AGENT_ROLE_NOT_ALLOWED",
                    message=f"node {node.node_id} requests disallowed agent role: {role}",
                    node_id=node.node_id,
                ))
        _validate_schema(node.input_schema, f"node {node.node_id}.input_schema", issues)
        _validate_schema(node.output_schema, f"node {node.node_id}.output_schema", issues)
        if len(node.context_references) > 10:
            issues.append(ValidationIssue(
                code="TOO_MANY_CONTEXT_REFERENCES",
                message=f"node {node.node_id} requests {len(node.context_references)} context references (max 10)",
                node_id=node.node_id,
            ))
        for dep in node.dependencies:
            if dep == node.node_id:
                issues.append(ValidationIssue(code="SELF_DEPENDENCY", message="self dependency", node_id=node.node_id))

    seen_edges: set[tuple[str, str]] = set()
    for edge in proposal.edges:
        if edge.edge_id and len(edge.edge_id) > 128:
            issues.append(ValidationIssue(code="INVALID_EDGE_ID", message="edge_id too long", edge_id=edge.edge_id))
        if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
            issues.append(ValidationIssue(
                code="DANGLING_EDGE",
                message=f"edge references unknown node: {edge.source_node_id} -> {edge.target_node_id}",
                edge_id=edge.edge_id or None,
            ))
            continue
        if edge.condition is not None:
            issues.append(ValidationIssue(
                code="CONDITION_UNSUPPORTED",
                message="dynamic edge conditions are reserved and not supported in phase 1",
                edge_id=edge.edge_id or None,
            ))
        key = (edge.source_node_id, edge.target_node_id)
        if key in seen_edges:
            issues.append(ValidationIssue(code="DUPLICATE_EDGE", message="duplicate edge", edge_id=edge.edge_id or None))
        seen_edges.add(key)

    order, has_cycle, max_depth = topological_order(node_ids, proposal.edges)
    if has_cycle:
        issues.append(ValidationIssue(code="CYCLE_DETECTED", message="graph contains a dependency cycle"))
    if max_depth > limits.max_graph_depth:
        issues.append(ValidationIssue(
            code="GRAPH_TOO_DEEP", message=f"graph depth {max_depth} > {limits.max_graph_depth}",
        ))

    # 互相独立的节点是合法入口兼终点，必须允许并行执行。
    # 仅当节点被 edge/dependency 引用但定义缺失时才算孤立，该情况由 DANGLING_EDGE 报告。

    entries = [nid for nid in node_ids if all(e.target_node_id != nid for e in proposal.edges)]
    terminals = [nid for nid in node_ids if all(e.source_node_id != nid for e in proposal.edges)]
    if not entries:
        issues.append(ValidationIssue(code="NO_ENTRY", message="graph has no entry node"))
    if not terminals:
        issues.append(ValidationIssue(code="NO_TERMINAL", message="graph has no reachable final node"))

    # 声明的 dependencies 必须与边一致（提案可能只给其一，规范化时补齐）。
    for node in nodes:
        declared = set(node.dependencies)
        from_edges = {e.source_node_id for e in proposal.edges if e.target_node_id == node.node_id}
        if declared and declared != from_edges:
            issues.append(ValidationIssue(
                code="DEPENDENCY_MISMATCH",
                message=f"node {node.node_id} dependencies do not match edges",
                node_id=node.node_id,
            ))

    valid = not issues
    return ValidationReport(valid=valid, issues=issues, execution_order=order, max_depth=max_depth)


def build_definition(
    proposal: GraphProposal,
    report: ValidationReport,
    *,
    graph_id: str,
    run_id: str,
    tenant_id: str,
    graph_version: int = 1,
) -> GraphDefinition:
    """仅在 report.valid=True 时调用；规范化边 ID 与 dependencies。"""
    if not report.valid:
        raise ValueError("cannot build definition from an invalid proposal")
    order_index = {nid: idx for idx, nid in enumerate(report.execution_order)}
    normalized_edges = []
    for idx, edge in enumerate(proposal.edges):
        item = edge.model_copy(deep=True)
        if not item.edge_id:
            item.edge_id = f"e-{idx + 1}"
        item.graph_id = graph_id
        normalized_edges.append(item)
    dependency_map: dict[str, list[str]] = {node.node_id: [] for node in proposal.nodes}
    for edge in normalized_edges:
        dependency_map[edge.target_node_id].append(edge.source_node_id)
    nodes = []
    for node in proposal.nodes:
        item = node.model_copy(deep=True)
        item.dependencies = sorted(set(dependency_map[item.node_id]))
        item.execution_order = order_index.get(item.node_id, item.execution_order)
        nodes.append(item)
    nodes.sort(key=lambda item: item.execution_order)
    return GraphDefinition(
        graph_id=graph_id,
        run_id=run_id,
        tenant_id=tenant_id,
        graph_version=graph_version,
        graph_status="READY",
        root_goal=proposal.root_goal,
        acceptance_criteria=list(proposal.acceptance_criteria),
        max_concurrency=proposal.max_concurrency,
        failure_policy=proposal.failure_policy,
        nodes=nodes,
        edges=normalized_edges,
    )
