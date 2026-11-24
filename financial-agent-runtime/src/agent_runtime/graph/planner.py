"""LLM Graph Planning：提案生成与 Demo 回退（只在 Activity 中调用）。"""
from __future__ import annotations

import json
from typing import Any

from agent_runtime.agent.model import model_adapter
from agent_runtime.config import settings
from agent_runtime.graph.models import GraphProposal

GRAPH_PLANNING_SYSTEM_PROMPT = """You are a task graph planner for a financial-services agent runtime.
Decompose the user goal into a small directed acyclic graph (DAG) of concrete subtasks.
Return exactly one JSON object (no Markdown) with this schema:
{
  "root_goal": "string",
  "acceptance_criteria": ["string"],
  "max_concurrency": 5,
  "failure_policy": "CONTINUE_INDEPENDENT|FAIL_FAST|MANUAL_INTERVENTION",
  "nodes": [
    {
      "node_id": "snake_case_unique_id",
      "node_type": "task|analysis|aggregation|decision",
      "name": "short name",
      "description": "what this node must do",
      "dependencies": ["other_node_id"],
      "required_permissions": [],
      "risk_level": "low|medium|high",
      "context_policy": "summary_only|full|refs_only",
      "timeout_seconds": 300,
      "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    }
  ],
  "edges": [
    {"source_node_id": "a", "target_node_id": "b", "dependency_type": "SUCCESS"}
  ]
}
Rules:
- node_id must be unique and match ^[A-Za-z0-9_.\\-]+$.
- Edges must reference existing nodes; no cycles; no self-loops.
- Only use permissions explicitly provided in the user message; never invent permissions or high-risk operations.
- Prefer 2-4 independent tasks followed by one aggregation node.
- Every node must be connected and the graph must have at least one entry and one final node.
- Do not set edge conditions; they are reserved.
- Output schemas must only use type/properties/required/items/enum/description."""


def _demo_proposal(goal: str) -> dict[str, Any]:
    return {
        "root_goal": goal,
        "acceptance_criteria": ["覆盖任务目标", "明确标注数据缺口"],
        "max_concurrency": 3,
        "failure_policy": "CONTINUE_INDEPENDENT",
        "nodes": [
            {
                "node_id": "collect_inputs",
                "node_type": "task",
                "name": "收集输入",
                "description": f"收集与任务相关的最小必要信息：{goal[:200]}",
                "dependencies": [],
                "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                "context_policy": "summary_only",
            },
            {
                "node_id": "analyze_facts",
                "node_type": "analysis",
                "name": "分析事实",
                "description": f"基于已收集信息分析事实与不确定性：{goal[:200]}",
                "dependencies": [],
                "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                "context_policy": "summary_only",
            },
            {
                "node_id": "final_report",
                "node_type": "aggregation",
                "name": "汇总报告",
                "description": "汇总上游节点结果，明确列出失败或缺失的数据缺口，不得推断未验证结论。",
                "dependencies": ["collect_inputs", "analyze_facts"],
                "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                "context_policy": "refs_only",
            },
        ],
        "edges": [
            {"source_node_id": "collect_inputs", "target_node_id": "final_report", "dependency_type": "SUCCESS"},
            {"source_node_id": "analyze_facts", "target_node_id": "final_report", "dependency_type": "SUCCESS"},
        ],
    }


async def plan_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """返回未经信任的提案字典，必须由 validate_proposal 校验。"""
    goal = str(payload.get("root_goal") or "")
    allowed_permissions = sorted(set(payload.get("allowed_permissions") or []))
    if not settings.model_api_key:
        return _demo_proposal(goal)
    user = json.dumps({
        "root_goal": goal,
        "acceptance_criteria": payload.get("acceptance_criteria") or [],
        "allowed_permissions": allowed_permissions,
        "max_nodes": payload.get("max_nodes", settings.graph_max_nodes),
        "max_concurrency": payload.get("max_concurrency", settings.graph_max_concurrency),
        "available_permissions": allowed_permissions,
    }, ensure_ascii=False)
    proposal = await model_adapter.complete_json(
        GRAPH_PLANNING_SYSTEM_PROMPT,
        user,
        model_name=payload.get("model_name") or settings.model_name,
    )
    if not isinstance(proposal, dict):
        raise ValueError("planning model returned a non-object proposal")
    return GraphProposal.model_validate(proposal).model_dump(mode="json")
