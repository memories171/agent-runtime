"""Task Graph Temporal Activities：所有 DB/LLM 副作用只能在 Activity 中执行。"""
from __future__ import annotations

from typing import Any

from temporalio import activity

from agent_runtime.config import settings
from agent_runtime.graph.models import (
    GraphDefinition, GraphEvent, GraphEventType, GraphLimits, GraphProposal, NodeResult,
)
from agent_runtime.graph.planner import plan_graph
from agent_runtime.graph.service import (
    append_graph_event,
    latest_definition_for_run,
    load_definition,
    save_node_result,
    update_graph_status,
    update_node_status,
    upsert_definition,
)
from agent_runtime.graph.validate import ValidationReport, build_definition, validate_proposal


@activity.defn
async def plan_graph_activity(payload: dict[str, Any]) -> dict[str, Any]:
    activity.heartbeat({"stage": "graph_planning", "run_id": payload.get("run_id")})
    return await plan_graph(payload)


@activity.defn
async def persist_graph_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """校验通过的提案才会落库；返回冻结的 GraphDefinition。"""
    proposal = GraphProposal.model_validate(payload["proposal"])
    limits = GraphLimits.model_validate(payload.get("limits") or settings.graph_limits.model_dump(mode="json"))
    report = validate_proposal(
        proposal,
        limits=limits,
        allowed_permissions=set(payload.get("allowed_permissions") or []),
        graph_id=payload["graph_id"],
        run_id=payload["run_id"],
        tenant_id=payload["tenant_id"],
        graph_version=int(payload.get("graph_version", 1)),
    )
    if not report.valid:
        raise ValueError("graph validation failed: " + "; ".join(
            f"{issue.code}: {issue.message}" for issue in report.issues
        ))
    definition = build_definition(
        proposal, report,
        graph_id=payload["graph_id"],
        run_id=payload["run_id"],
        tenant_id=payload["tenant_id"],
        graph_version=int(payload.get("graph_version", 1)),
    )
    return await upsert_definition(
        definition, validation=report, planner_model=payload.get("planner_model")
    )


@activity.defn
async def validate_graph_proposal_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """供 Replan 使用：只校验不落库。"""
    proposal = GraphProposal.model_validate(payload["proposal"])
    limits = GraphLimits.model_validate(payload.get("limits") or settings.graph_limits.model_dump(mode="json"))
    report = validate_proposal(
        proposal,
        limits=limits,
        allowed_permissions=set(payload.get("allowed_permissions") or []),
        graph_id=payload["graph_id"],
        run_id=payload["run_id"],
        tenant_id=payload["tenant_id"],
        graph_version=int(payload.get("graph_version", 1)),
    )
    return report.model_dump(mode="json")


@activity.defn
async def load_graph_activity(payload: dict[str, Any]) -> dict[str, Any]:
    definition = await load_definition(
        payload["tenant_id"], payload["graph_id"], payload.get("graph_version")
    )
    if definition is None:
        raise ValueError("graph definition not found")
    return definition.model_dump(mode="json")


@activity.defn
async def load_latest_graph_for_run_activity(payload: dict[str, Any]) -> dict[str, Any] | None:
    definition = await latest_definition_for_run(payload["tenant_id"], payload["run_id"])
    return definition.model_dump(mode="json") if definition else None


@activity.defn
async def append_graph_event_activity(payload: dict[str, Any]) -> dict[str, Any]:
    event = GraphEvent.model_validate(payload)
    stored = await append_graph_event(event)
    return stored.model_dump(mode="json")


@activity.defn
async def save_node_result_activity(payload: dict[str, Any]) -> None:
    await save_node_result(NodeResult.model_validate(payload))


@activity.defn
async def update_node_status_activity(payload: dict[str, Any]) -> None:
    await update_node_status(
        payload["tenant_id"], payload["graph_id"], payload["node_key"],
        payload["status"], **{k: v for k, v in payload.items() if k not in {
            "tenant_id", "graph_id", "node_key", "status",
        }},
    )


@activity.defn
async def update_graph_status_activity(payload: dict[str, Any]) -> None:
    await update_graph_status(payload["tenant_id"], payload["graph_id"], payload["graph_status"])


@activity.defn
async def replan_graph_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """受控 Replan：基于提案生成新版本，原版本与历史保留。"""
    base = await load_definition(payload["tenant_id"], payload["graph_id"], payload.get("base_version"))
    if base is None:
        raise ValueError("base graph definition not found")
    proposal = GraphProposal.model_validate(payload["proposal"])
    limits = GraphLimits.model_validate(payload.get("limits") or settings.graph_limits.model_dump(mode="json"))
    report = validate_proposal(
        proposal,
        limits=limits,
        allowed_permissions=set(payload.get("allowed_permissions") or []),
        graph_id=base.graph_id,
        run_id=base.run_id,
        tenant_id=base.tenant_id,
        graph_version=base.graph_version + 1,
    )
    if not report.valid:
        raise ValueError("replan validation failed: " + "; ".join(
            f"{issue.code}: {issue.message}" for issue in report.issues
        ))
    definition = build_definition(
        proposal, report,
        graph_id=base.graph_id,
        run_id=base.run_id,
        tenant_id=base.tenant_id,
        graph_version=base.graph_version + 1,
    )
    return await upsert_definition(definition, validation=report, planner_model=payload.get("planner_model"))
