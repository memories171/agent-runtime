"""Graph 查询与 Replan API：复用 Run ACL，不建立第二套 Run 管理接口。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import can_access_run, can_manage_run, get_principal
from agent_runtime.config import settings
from agent_runtime.contracts import Principal
from agent_runtime.db import (
    TaskGraphRecord, fetch_run, get_session, session_for_tenant,
)
from agent_runtime.graph.models import GraphEvent, GraphProposal
from agent_runtime.graph.service import (
    append_graph_event, list_events, list_nodes, load_definition,
    latest_definition_for_run, get_node, upsert_definition,
)
from agent_runtime.graph.validate import build_definition, validate_proposal

router = APIRouter()


class ReplanRequest(BaseModel):
    proposed_graph: dict[str, Any]
    reason: str | None = Field(default=None, max_length=2_000)


async def _check_run_access(session: AsyncSession, principal: Principal, run_id: UUID, manage: bool = False) -> None:
    run = await fetch_run(session, run_id, principal.tenant_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="run not found")
    allowed = can_manage_run(principal, run.user_id) if manage else can_access_run(principal, run.user_id)
    if not allowed:
        raise HTTPException(status_code=404, detail="run not found")


async def _load_graph_row(tenant_id: str, graph_id: str) -> TaskGraphRecord:
    async with session_for_tenant(tenant_id) as session:
        row = await session.get(TaskGraphRecord, graph_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="graph not found")
    return row


async def _graph_payload(tenant_id: str, graph_id: str, version: int | None = None) -> dict[str, Any]:
    definition = await load_definition(tenant_id, graph_id, version)
    if definition is None:
        raise HTTPException(status_code=404, detail="graph not found")
    nodes = await list_nodes(tenant_id, graph_id)
    return {
        **definition.model_dump(mode="json"),
        "nodes": nodes,
        "node_count": len(nodes),
    }


@router.get("/api/v1/runs/{run_id}/graph")
@router.get("/v1/runs/{run_id}/graph")
async def get_run_graph(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _check_run_access(session, principal, run_id)
    definition = await latest_definition_for_run(principal.tenant_id, str(run_id))
    if definition is None:
        raise HTTPException(status_code=404, detail="graph not found for run")
    return await _graph_payload(principal.tenant_id, definition.graph_id, definition.graph_version)


@router.get("/api/v1/runs/{run_id}/graph/nodes")
@router.get("/v1/runs/{run_id}/graph/nodes")
async def get_run_graph_nodes(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _check_run_access(session, principal, run_id)
    definition = await latest_definition_for_run(principal.tenant_id, str(run_id))
    if definition is None:
        raise HTTPException(status_code=404, detail="graph not found for run")
    nodes = await list_nodes(principal.tenant_id, definition.graph_id)
    return {"graph_id": definition.graph_id, "graph_version": definition.graph_version, "nodes": nodes}


@router.get("/api/v1/graphs/{graph_id}")
@router.get("/v1/graphs/{graph_id}")
async def get_graph(graph_id: str, principal: Principal = Depends(get_principal)):
    row = await _load_graph_row(principal.tenant_id, graph_id)
    return await _graph_payload(principal.tenant_id, graph_id, row.graph_version)


@router.get("/api/v1/graphs/{graph_id}/events")
@router.get("/v1/graphs/{graph_id}/events")
async def get_graph_events(
    graph_id: str,
    limit: int = 500,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    row = await _load_graph_row(principal.tenant_id, graph_id)
    await _check_run_access(session, principal, row.run_id)
    events = await list_events(principal.tenant_id, graph_id, max(1, min(limit, 1000)))
    return {"graph_id": graph_id, "events": events}


@router.get("/api/v1/graphs/{graph_id}/nodes/{node_id}")
@router.get("/v1/graphs/{graph_id}/nodes/{node_id}")
async def get_graph_node(
    graph_id: str,
    node_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    row = await _load_graph_row(principal.tenant_id, graph_id)
    await _check_run_access(session, principal, row.run_id)
    node = await get_node(principal.tenant_id, graph_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="graph node not found")
    events = await list_events(principal.tenant_id, graph_id)
    return {
        **node,
        "events": [event for event in events if event.get("node_id") == node_id],
    }


@router.post("/api/v1/graphs/{graph_id}/replan")
@router.post("/v1/graphs/{graph_id}/replan")
async def replan_graph(
    graph_id: str,
    body: ReplanRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if "agent:execute" not in principal.scopes:
        raise HTTPException(status_code=403, detail="agent:execute scope required")
    row = await _load_graph_row(principal.tenant_id, graph_id)
    await _check_run_access(session, principal, row.run_id, manage=True)
    base = await load_definition(principal.tenant_id, graph_id, row.graph_version)
    if base is None:
        raise HTTPException(status_code=404, detail="graph definition not found")
    if base.graph_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        raise HTTPException(status_code=409, detail="cannot replan a terminal graph")

    proposal = GraphProposal.model_validate(body.proposed_graph)
    limits = settings.graph_limits
    allowed = set(principal.permissions) | set(principal.scopes)
    report = validate_proposal(
        proposal,
        limits=limits,
        allowed_permissions=allowed,
        graph_id=graph_id,
        run_id=str(base.run_id),
        tenant_id=principal.tenant_id,
        graph_version=base.graph_version + 1,
    )
    if not report.valid:
        raise HTTPException(status_code=422, detail={
            "message": "graph proposal rejected",
            "issues": [issue.model_dump() for issue in report.issues],
        })
    definition = build_definition(
        proposal, report,
        graph_id=graph_id,
        run_id=str(base.run_id),
        tenant_id=principal.tenant_id,
        graph_version=base.graph_version + 1,
    )
    await upsert_definition(definition, validation=report, planner_model=principal.user_id)
    await append_graph_event(GraphEvent(
        graph_id=graph_id,
        run_id=str(base.run_id),
        tenant_id=principal.tenant_id,
        event_type="GRAPH_REPLANNED",
        payload={"from_version": base.graph_version, "to_version": definition.graph_version, "reason": body.reason},
    ))
    return await _graph_payload(principal.tenant_id, graph_id, definition.graph_version)
