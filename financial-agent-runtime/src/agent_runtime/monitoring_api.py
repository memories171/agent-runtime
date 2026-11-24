"""Parallel Monitoring read-only API (3.3.6).

Step 2 exposes the Agent Tree endpoints. All endpoints enforce the same
Tenant + Resource ACL as the Run API:

- tenant identity always comes from the authenticated principal, never from a
  client-supplied query/body parameter;
- the requested execution_id (external name of run_id) is resolved through the
  Run ACL first, so a caller cannot probe another tenant's or another user's run;
- every tree node is checked individually via DelegationService.get_agent_tree_view,
  and every anomaly edge is checked via detect_tree_anomalies;
- responses never disclose hidden node ids, names, errors or counts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import get_principal
from agent_runtime.contracts import (
    AgentTreeAnomaliesRead,
    AgentTreeRead,
    ExecutionMetricsRead,
    Principal,
)
from agent_runtime.db import get_session
from agent_runtime.monitoring.metrics_service import ExecutionMetricsService
from agent_runtime.orchestration.delegation_service import DelegationService

router = APIRouter()

MAX_TREE_DEPTH = 32
MAX_TREE_NODES = 500


async def _owned_run(session: AsyncSession, run_id: UUID, principal: Principal):
    from agent_runtime.api import _get_owned_run

    return await _get_owned_run(session, run_id, principal, "read")


@router.get("/api/v1/monitoring/executions/{execution_id}/tree", response_model=AgentTreeRead)
async def get_execution_tree(
    execution_id: UUID,
    max_depth: int | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentTreeRead:
    """Return the ACL-filtered Agent Tree rooted at `execution_id`.

    `max_depth` is optional and bounded to [0, 32]. Exceeding nodes or depth are
    reported only through `has_hidden_children` (no count or identifiers).
    """
    if max_depth is not None and not 0 <= max_depth <= MAX_TREE_DEPTH:
        raise HTTPException(status_code=400, detail=f"max_depth must be between 0 and {MAX_TREE_DEPTH}")
    await _owned_run(session, execution_id, principal)
    return await DelegationService.get_agent_tree_view(
        tenant_id=principal.tenant_id,
        root_run_id=execution_id,
        principal=principal,
        max_depth=max_depth,
        max_nodes=MAX_TREE_NODES,
    )


@router.get(
    "/api/v1/monitoring/executions/{execution_id}/tree/anomalies",
    response_model=AgentTreeAnomaliesRead,
)
async def get_execution_tree_anomalies(
    execution_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentTreeAnomaliesRead:
    """Return integrity diagnostics (orphans / cycles / duplicate links).

    The Root Run ACL is checked first, then every edge is filtered by the Run
    ACL independently (principal is passed through). Hidden nodes never leak
    their UUID, path, structure, count or error; tenant always comes from the
    authenticated principal.
    """
    await _owned_run(session, execution_id, principal)
    return await DelegationService.detect_tree_anomalies(
        tenant_id=principal.tenant_id,
        root_run_id=execution_id,
        principal=principal,
    )


def _validate_window(start_time: datetime, end_time: datetime) -> None:
    if start_time.tzinfo is None or end_time.tzinfo is None:
        raise HTTPException(status_code=400, detail="timestamps must be timezone-aware")
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="end_time must be greater than start_time")


@router.get("/api/v1/monitoring/runs/{run_id}/metrics", response_model=ExecutionMetricsRead)
async def get_execution_metrics(
    run_id: UUID,
    start_time: datetime,
    end_time: datetime,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ExecutionMetricsRead:
    """Return delegated-agent parallelism metrics for an owned execution.

    The Root Agent is excluded because the current data model has no reliable
    Root Agent execution interval. Hidden child runs are excluded from both the
    aggregate values and the reason codes' detail; only generic codes are sent.
    """
    _validate_window(start_time, end_time)
    await _owned_run(session, run_id, principal)
    return await ExecutionMetricsService.collect(
        tenant_id=principal.tenant_id,
        root_run_id=run_id,
        principal=principal,
        start_time=start_time,
        end_time=end_time,
        now=datetime.now(timezone.utc),
    )
