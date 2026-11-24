"""Context Manager read-only API.

Every endpoint enforces the same Tenant + Resource ACL as the Run API. All
responses are display-safe (secrets masked) and carry provenance.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import get_principal
from agent_runtime.config import settings
from agent_runtime.contracts import Principal
from agent_runtime.db import RunTelemetryRecord, get_session

router = APIRouter()


async def _require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(status_code=403, detail=f"{scope} scope required")


async def _owned_run(session: AsyncSession, run_id: UUID, principal: Principal):
    from agent_runtime.api import _get_owned_run

    return await _get_owned_run(session, run_id, principal, "read")


async def _persistence():
    from agent_runtime.checkpoint.storage import build_snapshot_store
    from agent_runtime.context.service import ContextPersistence

    return ContextPersistence(build_snapshot_store(settings))


@router.get("/api/v1/runs/{run_id}/context")
async def get_run_context(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _require_scope(principal, "run:context:read")
    await _owned_run(session, run_id, principal)
    persistence = await _persistence()
    pack = await persistence.load_pack(session, principal.tenant_id, str(run_id))
    if pack is None:
        raise HTTPException(status_code=404, detail="context pack not found")
    return pack.display_safe()


@router.get("/api/v1/runs/{run_id}/context/history")
async def get_run_context_history(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _require_scope(principal, "run:context:read")
    await _owned_run(session, run_id, principal)
    persistence = await _persistence()
    return {"items": await persistence.list_packs(session, principal.tenant_id, str(run_id))}


@router.get("/api/v1/runs/{run_id}/context/summaries")
async def get_run_context_summaries(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _require_scope(principal, "run:context:read")
    await _owned_run(session, run_id, principal)
    persistence = await _persistence()
    return {"items": await persistence.list_summaries(session, principal.tenant_id, str(run_id))}


@router.get("/api/v1/runs/{run_id}/context/metrics")
async def get_run_context_metrics(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _require_scope(principal, "run:context:read")
    await _owned_run(session, run_id, principal)
    telemetry = (
        await session.execute(
            select(RunTelemetryRecord)
            .where(RunTelemetryRecord.run_id == run_id)
            .order_by(desc(RunTelemetryRecord.created_at))
            .limit(1)
        )
    ).scalars().first()
    llm_calls = (telemetry.llm_calls_json or []) if telemetry else []
    context_calls = [c for c in llm_calls if isinstance(c, dict) and c.get("context_pack_id")]
    reported = [c for c in llm_calls if isinstance(c, dict) and c.get("provider_reported")]
    deltas = [c["token_delta"] for c in reported if c.get("token_delta") is not None]
    persistence = await _persistence()
    packs = await persistence.list_packs(session, principal.tenant_id, str(run_id), limit=500)
    return {
        "context_builds": len(packs),
        "context_calls": len(context_calls),
        "compressions": sum(int(p.get("compression_count") or 0) for p in packs),
        "degraded_builds": sum(1 for p in packs if p.get("degraded")),
        "provider_reported_calls": len(reported),
        "token_delta_samples": deltas,
        "avg_token_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
    }
