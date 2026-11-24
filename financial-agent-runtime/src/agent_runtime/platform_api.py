from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import can_access_run, get_principal, require_permission
from agent_runtime.config import settings
from agent_runtime.contracts import MemoryCreate, MemoryRead, MemoryUpdate, Principal
from agent_runtime.db import (
    AuthAuditLog, CheckpointRecord, FinancialAuditLog, FinancialResourceGrant, MemoryRecord,
    Permission, ResourceAcl, Role, RolePermission, RunEvent, RunRecord, RunTelemetryRecord,
    SkillState, Tenant, TenantMembership, ToolInvocation, User, UserRole, WorkerFailureRecord, get_session,
)
from agent_runtime.recovery.invocations import (
    InvocationConflictError,
    ToolInvocationService,
    human_reconciliation_fencing_token,
)
from agent_runtime.memory.service import (
    archive_memory, create_memory, delete_memory, get_memory, list_memories, search_memories,
    to_memory_read, update_memory,
)
from agent_runtime.skills.registry import SkillRegistry
from agent_runtime.tools.registry import _mask_url, mcp_server_status, registry

router = APIRouter()

SKILL_TOOLS: dict[str, list[str]] = {
    "transaction-monitoring": ["financial.list_transactions", "financial.screen_transactions"],
    "kyc-review": ["financial.get_customer_profile", "financial.get_account_summary"],
    "financial-research": ["demo.echo"],
    "financial-reconciliation": ["financial.list_transactions"],
    "credit-risk-review": ["financial.get_customer_profile", "demo.echo"],
}
SKILL_SCOPES: dict[str, list[str]] = {
    "transaction-monitoring": ["financial:transactions:read", "financial:monitoring:run"],
    "kyc-review": ["financial:customer:read", "financial:case:read"],
    "financial-research": ["agent:execute"],
    "financial-reconciliation": ["financial:transactions:read"],
    "credit-risk-review": ["financial:customer:read", "agent:execute"],
}


class SkillEnabled(BaseModel):
    enabled: bool


class ReconcileRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=256)
    resolved_status: Literal["CONFIRMED", "FAILED"]
    resolved_result: dict[str, Any] | None = None
    note: str = Field(default="", max_length=1000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _owned_run(session: AsyncSession, run_id: UUID, principal: Principal) -> RunRecord:
    run = await session.get(RunRecord, run_id)
    if run is None or run.tenant_id != principal.tenant_id or not can_access_run(principal, run.user_id):
        raise HTTPException(status_code=404, detail="run not found")
    return run


async def _checkpoint_ids(session: AsyncSession, run_id: UUID) -> list[str]:
    rows = (await session.execute(
        select(CheckpointRecord.checkpoint_id).where(CheckpointRecord.run_id == run_id)
    )).scalars()
    return [str(row) for row in rows]


async def _failure_count(session: AsyncSession, run_id: UUID) -> int:
    return len((await session.execute(
        select(WorkerFailureRecord.failure_id).where(WorkerFailureRecord.run_id == run_id)
    )).scalars().all())


async def _telemetry_read(session: AsyncSession, run: RunRecord, telemetry: RunTelemetryRecord | None) -> dict[str, Any]:
    metadata = run.metadata_json or {}
    created = run.created_at or datetime.now(timezone.utc)
    updated = run.updated_at or created
    checkpoint_ids = await _checkpoint_ids(session, run.run_id)
    recovery_count = await _failure_count(session, run.run_id)
    return {
        "run_id": str(run.run_id),
        "model": (telemetry.model_name if telemetry else None) or metadata.get("model_name"),
        "harness_version": (telemetry.harness_version if telemetry else None) or metadata.get("harness_version"),
        "prompt_version": (telemetry.prompt_version if telemetry else None) or "RUNTIME-PROMPT-v1",
        "duration_seconds": int(metadata.get("duration_seconds") or max(0, (updated - created).total_seconds())),
        "heartbeat_at": _iso(telemetry.heartbeat_at if telemetry else None) or metadata.get("heartbeat_at"),
        "deadline_at": _iso(telemetry.deadline_at if telemetry else None) or metadata.get("deadline_at"),
        "current_step": (telemetry.current_step if telemetry else None) or metadata.get("current_step") or run.status,
        "progress_pct": (telemetry.progress_pct if telemetry else None) or metadata.get("progress_pct") or 0,
        "token_input": (telemetry.token_input if telemetry else None) or metadata.get("token_input") or 0,
        "token_output": (telemetry.token_output if telemetry else None) or metadata.get("token_output") or 0,
        "estimated_cost_usd": (telemetry.estimated_cost_usd if telemetry else None) or metadata.get("estimated_cost_usd") or 0.0,
        "tool_calls": (telemetry.tool_calls_json if telemetry else None) or metadata.get("tool_calls") or [],
        "llm_calls": (telemetry.llm_calls_json if telemetry else None) or metadata.get("llm_calls") or [],
        "checkpoint_ids": checkpoint_ids,
        "recovery_count": recovery_count,
    }


@router.get("/runs/telemetry")
async def list_run_telemetries(
    limit: int = 100,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    runs = list((await session.execute(
        select(RunRecord).where(RunRecord.tenant_id == principal.tenant_id)
        .order_by(RunRecord.created_at.desc()).limit(max(1, min(limit, 200)))
    )).scalars())
    results = []
    for run in runs:
        telemetry = (await session.execute(
            select(RunTelemetryRecord).where(RunTelemetryRecord.run_id == run.run_id)
        )).scalar_one_or_none()
        results.append(await _telemetry_read(session, run, telemetry))
    return results


@router.get("/runs/{run_id}/telemetry")
async def get_run_telemetry(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    run = await _owned_run(session, run_id, principal)
    telemetry = (await session.execute(
        select(RunTelemetryRecord).where(RunTelemetryRecord.run_id == run_id)
    )).scalar_one_or_none()
    return await _telemetry_read(session, run, telemetry)


@router.get("/runs/{run_id}/tool-calls")
async def get_tool_calls(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    telemetry = await get_run_telemetry(run_id, principal, session)
    return telemetry["tool_calls"]


@router.get("/runs/{run_id}/llm-calls")
async def get_llm_calls(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    telemetry = await get_run_telemetry(run_id, principal, session)
    return telemetry["llm_calls"]


@router.get("/mcp/servers")
async def list_mcp_servers(principal: Principal = Depends(get_principal)):
    return [record for record in mcp_server_status.values() if "url" in record]


@router.get("/mcp/servers/{server_id}")
async def get_mcp_server(server_id: str, principal: Principal = Depends(get_principal)):
    record = mcp_server_status.get(server_id)
    if record is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    return record


@router.get("/mcp/servers/{server_id}/tools")
async def get_mcp_server_tools(server_id: str, principal: Principal = Depends(get_principal)):
    prefix = f"{server_id}."
    return [{
        "name": tool.name,
        "display_name": tool.display_name,
        "server_id": server_id,
        "description": tool.description,
        "required_scopes": sorted(tool.required_scopes),
        "risk_level": tool.risk_level,
        "idempotent": tool.idempotent,
    } for tool in registry.list() if tool.name.startswith(prefix)]


@router.get("/mcp/tools")
async def list_mcp_tools(principal: Principal = Depends(get_principal)):
    return [{
        "name": tool.name,
        "display_name": tool.display_name,
        "server_id": _mask_url(tool.mcp_server_url) if tool.mcp_server_url else "internal",
        "description": tool.description,
        "required_scopes": sorted(tool.required_scopes),
        "risk_level": tool.risk_level,
        "idempotent": tool.idempotent,
    } for tool in registry.list() if tool.mcp_server_url]


@router.get("/skills/managed")
async def list_managed_skills(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    skills = SkillRegistry(settings.skills_dir).list_skills()
    states = {row.skill_id: row.enabled for row in (await session.execute(select(SkillState))).scalars()}
    results = []
    for skill in skills:
        skill_id = skill["skill_id"]
        results.append({
            "skill_id": skill_id,
            "name": skill.get("name") or skill_id,
            "display_name": skill.get("display_name") or skill_id,
            "description": skill.get("description", ""),
            "version": skill.get("version", "1.0.0"),
            "release_status": skill.get("release_status", "STABLE"),
            "tools": skill.get("tools") or SKILL_TOOLS.get(skill_id, []),
            "required_scopes": skill.get("required_scopes") or SKILL_SCOPES.get(skill_id, []),
            "updated_at": skill.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            "enabled": states.get(skill_id, True),
        })
    return results


@router.patch("/skills/{skill_id}/enabled")
async def set_skill_enabled(
    skill_id: str,
    body: SkillEnabled,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if not ({"skills:manage", "platform:manage"} & principal.scopes):
        raise HTTPException(status_code=403, detail="skills:manage scope required")
    state = await session.get(SkillState, skill_id)
    if state is None:
        state = SkillState(skill_id=skill_id, enabled=body.enabled)
        session.add(state)
    else:
        state.enabled = body.enabled
    await session.commit()
    return {"skill_id": skill_id, "enabled": body.enabled, "updated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/memory")
async def list_memory_records(
    q: str | None = None,
    type: str | None = None,
    scope: str | None = None,
    include_archived: bool = False,
    limit: int = 100,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "memory:read")
    rows = await list_memories(session, principal, query=q, memory_type=type, scope=scope,
                               include_archived=include_archived, limit=max(1, min(limit, 200)))
    return [to_memory_read(row).model_dump(mode="json") for row in rows]


@router.post("/memory/full", response_model=MemoryRead, status_code=201)
async def create_memory_full(
    body: MemoryCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "memory:write")
    try:
        record = await create_memory(
            session, principal, body.content, body.scope, body.memory_type, body.source_run_id,
            metadata=body.metadata, title=body.title, confidence=body.confidence,
            expires_at=body.expires_at, acl=body.acl,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return to_memory_read(record)


@router.get("/memory/search", response_model=list[MemoryRead])
async def search_memory_records(
    q: str,
    limit: int = 10,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "memory:read")
    rows = await search_memories(session, principal, q, max(1, min(limit, 50)))
    return [to_memory_read(row) for row in rows]


@router.get("/memory/{memory_id}", response_model=MemoryRead)
async def get_memory_record(
    memory_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await get_memory(session, principal, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return to_memory_read(record)


@router.patch("/memory/{memory_id}", response_model=MemoryRead)
async def update_memory_record(
    memory_id: UUID,
    body: MemoryUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await get_memory(session, principal, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    try:
        updated = await update_memory(
            session, principal, record,
            title=body.title, content=body.content, memory_type=body.memory_type, scope=body.scope,
            confidence=body.confidence, expires_at=body.expires_at, acl=body.acl,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return to_memory_read(updated)


@router.post("/memory/{memory_id}/archive", response_model=MemoryRead)
async def archive_memory_record(
    memory_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await get_memory(session, principal, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    try:
        updated = await archive_memory(session, principal, record)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return to_memory_read(updated)


@router.delete("/memory/{memory_id}", status_code=204)
async def delete_memory_record(
    memory_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await get_memory(session, principal, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory not found")
    try:
        await delete_memory(session, principal, record)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/checkpoints")
async def list_tenant_checkpoints(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(CheckpointRecord).where(CheckpointRecord.tenant_id == principal.tenant_id)
        .order_by(CheckpointRecord.created_at.desc())
    )).scalars()
    return [{
        "checkpoint_id": str(row.checkpoint_id), "run_id": str(row.run_id),
        "tenant_id": row.tenant_id, "created_at": row.created_at.isoformat() if row.created_at else None,
        "step": row.step, "summary": row.summary, "size_kb": row.size_kb,
        "status": row.status, "worker_id": row.worker_id,
        "idempotency_key": row.idempotency_key, "duplicate_protection": row.duplicate_protection,
    } for row in rows]


@router.get("/worker-failures")
async def list_tenant_worker_failures(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(WorkerFailureRecord).where(WorkerFailureRecord.tenant_id == principal.tenant_id)
        .order_by(WorkerFailureRecord.occurred_at.desc())
    )).scalars()
    return [{
        "failure_id": str(row.failure_id), "run_id": str(row.run_id),
        "tenant_id": row.tenant_id, "worker_id": row.worker_id,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "reason": row.reason, "recovery_status": row.recovery_status,
        "recovery_count": row.recovery_count, "retry_count": row.retry_count,
        "recovery_attempt": row.recovery_attempt, "activity_retry_count": row.activity_retry_count,
        "recovery_started_at": row.recovery_started_at.isoformat() if row.recovery_started_at else None,
        "recovery_completed_at": row.recovery_completed_at.isoformat() if row.recovery_completed_at else None,
        "fencing_token": row.fencing_token,
        "recovered_from_checkpoint_id": str(row.recovered_from_checkpoint_id) if row.recovered_from_checkpoint_id else None,
        "recovery_position": row.recovery_position, "idempotency_key": row.idempotency_key,
        "duplicate_protection": row.duplicate_protection,
    } for row in rows]


@router.get("/runs/{run_id}/checkpoints")
async def list_checkpoints(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _owned_run(session, run_id, principal)
    rows = (await session.execute(
        select(CheckpointRecord).where(CheckpointRecord.run_id == run_id)
        .order_by(CheckpointRecord.checkpoint_sequence.desc())
    )).scalars()
    return [{
        "checkpoint_id": str(row.checkpoint_id), "run_id": str(row.run_id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "step": row.step, "checkpoint_sequence": row.checkpoint_sequence,
        "summary": row.summary, "size_kb": row.size_kb,
        "blob_size_bytes": row.blob_size_bytes, "state_digest": row.state_digest,
        "state_blob_ref": row.state_blob_ref, "schema_version": row.schema_version,
        "persistence_status": row.persistence_status,
        "status": row.status, "worker_id": row.worker_id,
        "idempotency_key": row.idempotency_key, "duplicate_protection": row.duplicate_protection,
    } for row in rows]


@router.get("/checkpoints/{checkpoint_id}")
async def get_checkpoint(
    checkpoint_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    record = await session.get(CheckpointRecord, checkpoint_id)
    if record is None or record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    await _owned_run(session, record.run_id, principal)
    return {
        "checkpoint_id": str(record.checkpoint_id), "run_id": str(record.run_id),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "step": record.step, "checkpoint_sequence": record.checkpoint_sequence,
        "summary": record.summary, "size_kb": record.size_kb,
        "blob_size_bytes": record.blob_size_bytes, "state_digest": record.state_digest,
        "blob_sha256": record.blob_sha256, "state_blob_ref": record.state_blob_ref,
        "schema_version": record.schema_version, "compression_algorithm": record.compression_algorithm,
        "encryption_algorithm": record.encryption_algorithm, "encryption_key_id": record.encryption_key_id,
        "persistence_status": record.persistence_status, "status": record.status,
        "idempotency_key": record.idempotency_key,
        "envelope": record.envelope_json or {},
    }


@router.get("/runs/{run_id}/worker-failures")
async def list_worker_failures(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _owned_run(session, run_id, principal)
    rows = (await session.execute(
        select(WorkerFailureRecord).where(WorkerFailureRecord.run_id == run_id)
        .order_by(WorkerFailureRecord.occurred_at.desc())
    )).scalars()
    return [{
        "failure_id": str(row.failure_id), "run_id": str(row.run_id),
        "tenant_id": row.tenant_id, "worker_id": row.worker_id,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "reason": row.reason, "recovery_status": row.recovery_status,
        "recovery_count": row.recovery_count, "retry_count": row.retry_count,
        "recovery_attempt": row.recovery_attempt, "activity_retry_count": row.activity_retry_count,
        "recovery_started_at": row.recovery_started_at.isoformat() if row.recovery_started_at else None,
        "recovery_completed_at": row.recovery_completed_at.isoformat() if row.recovery_completed_at else None,
        "fencing_token": row.fencing_token,
        "recovered_from_checkpoint_id": str(row.recovered_from_checkpoint_id) if row.recovered_from_checkpoint_id else None,
        "recovery_position": row.recovery_position, "idempotency_key": row.idempotency_key,
        "duplicate_protection": row.duplicate_protection,
    } for row in rows]


@router.get("/runs/{run_id}/recovery-timeline")
async def get_recovery_timeline(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    run = await _owned_run(session, run_id, principal)
    checkpoints = await list_checkpoints(run_id, principal, session)
    failures = await list_worker_failures(run_id, principal, session)
    latest_failure = failures[0] if failures else None
    failed_time = latest_failure["occurred_at"] if latest_failure else None
    recovery_started = latest_failure["recovery_started_at"] if latest_failure else None
    recovery_completed = latest_failure["recovery_completed_at"] if latest_failure else None
    attempt = latest_failure["recovery_attempt"] if latest_failure else 0
    return [
        {"key": "running", "label": "RUNNING", "time": run.created_at.isoformat() if run.created_at else "—",
         "status": "finish", "description": "工作流已启动并进入持久化执行。"},
        {"key": "checkpoint", "label": "CHECKPOINT_SAVED", "time": checkpoints[0]["created_at"] if checkpoints else "未保存",
         "status": "finish" if checkpoints else "wait", "description": checkpoints[0]["summary"] if checkpoints else "尚未保存检查点。"},
        {"key": "failed", "label": "WORKER_FAILED", "time": failed_time or "未发生",
         "status": "error" if latest_failure else "wait", "description": latest_failure["reason"] if latest_failure else "未记录 Worker 故障。"},
        {"key": "replay", "label": "TEMPORAL_HISTORY_REPLAY", "time": recovery_started or "未发生",
         "status": "finish" if recovery_started else "wait",
         "description": f"Temporal 历史事件重放，恢复尝试 {attempt}。", "recovery_attempt": attempt},
        {"key": "reconnected", "label": "WORKER_RECONNECTED", "time": recovery_started or "未发生",
         "status": "finish" if recovery_started else "wait",
         "description": "新 Worker 已接管同一 Workflow，未创建新 Run。", "fencing_token": latest_failure["fencing_token"] if latest_failure else 0},
        {"key": "recovering", "label": "RECOVERING", "time": recovery_started or failed_time or "未发生",
         "status": "process" if latest_failure and latest_failure["recovery_status"] == "RECOVERING" else "wait",
         "description": f"恢复次数 {latest_failure['recovery_count'] if latest_failure else 0}，重试 {latest_failure['retry_count'] if latest_failure else 0}。",
         "recovery_attempt": attempt, "activity_retry_count": latest_failure["activity_retry_count"] if latest_failure else 0},
        {"key": "resumed", "label": "WORKFLOW_RESUMED", "time": recovery_completed or "未恢复",
         "status": "finish" if latest_failure and latest_failure["recovery_status"] == "RECOVERED" else "wait",
         "description": f"恢复位置：{latest_failure['recovery_position'] if latest_failure else '—'}"},
        {"key": "succeeded", "label": "SUCCEEDED", "time": run.updated_at.isoformat() if run.status == "SUCCEEDED" and run.updated_at else "未完成",
         "status": "finish" if run.status == "SUCCEEDED" else "wait", "description": f"当前状态：{run.status}"},
    ]


def _invocation_read(row: ToolInvocation) -> dict[str, Any]:
    return {
        "invocation_id": str(row.invocation_id), "tenant_id": row.tenant_id,
        "run_id": str(row.run_id), "tool_call_id": row.tool_call_id,
        "tool_name": row.tool_name, "idempotency_key": row.idempotency_key,
        "canonical_arguments_hash": row.canonical_arguments_hash,
        "status": row.status, "attempt_count": row.attempt_count,
        "external_reference": row.external_reference, "result_reference": row.result_reference,
        "reconciliation_status": row.reconciliation_status,
        "reconciliation_note": row.reconciliation_note,
        "fencing_token": row.fencing_token, "lease_owner": row.lease_owner,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/runs/{run_id}/tool-invocations")
async def list_tool_invocations(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _owned_run(session, run_id, principal)
    rows = (await session.execute(
        select(ToolInvocation).where(
            ToolInvocation.run_id == run_id,
            ToolInvocation.tenant_id == principal.tenant_id,
        ).order_by(ToolInvocation.created_at.desc())
    )).scalars()
    return [_invocation_read(row) for row in rows]


@router.post("/runs/{run_id}/reconcile")
async def reconcile_invocation(
    run_id: UUID,
    body: ReconcileRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    await _owned_run(session, run_id, principal)
    if "approval:approve" not in principal.scopes and "runs:manage:tenant" not in principal.scopes:
        raise HTTPException(status_code=403, detail="approval:approve or runs:manage:tenant scope required")
    service = ToolInvocationService()
    record = (await session.execute(
        select(ToolInvocation).where(
            ToolInvocation.tenant_id == principal.tenant_id,
            ToolInvocation.idempotency_key == body.idempotency_key,
            ToolInvocation.run_id == run_id,
        )
    )).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="invocation not found")
    try:
        result = await service.reconcile(
            session,
            tenant_id=principal.tenant_id,
            idempotency_key=body.idempotency_key,
            fencing_token=human_reconciliation_fencing_token(record),
            resolved_status=body.resolved_status,
            resolved_result=body.resolved_result,
            actor=principal.user_id,
            note=body.note,
        )
        await session.commit()
        return result
    except InvocationConflictError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/recovery/stats")
async def get_recovery_stats(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    failures = list((await session.execute(
        select(WorkerFailureRecord).where(WorkerFailureRecord.tenant_id == principal.tenant_id)
    )).scalars())
    recovered = sum(1 for row in failures if row.recovery_status == "RECOVERED")
    checkpoints = len((await session.execute(
        select(CheckpointRecord.checkpoint_id).where(CheckpointRecord.tenant_id == principal.tenant_id)
    )).scalars().all())
    return {
        "checkpoints": checkpoints,
        "failures": len(failures),
        "recovered": recovered,
        "recovery_success_rate": round(recovered / len(failures) * 100, 1) if failures else 0,
        "average_retries": round(sum(row.retry_count for row in failures) / len(failures), 1) if failures else 0,
    }


@router.get("/security/snapshot")
async def get_security_snapshot(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if not ({"security:read", "financial:audit:read", "platform:manage"} & principal.scopes):
        raise HTTPException(status_code=403, detail="security:read scope required")
    tenant_id = principal.tenant_id
    runs = list((await session.execute(
        select(RunRecord).where(RunRecord.tenant_id == tenant_id)
    )).scalars())
    grants = list((await session.execute(
        select(FinancialResourceGrant).where(FinancialResourceGrant.tenant_id == tenant_id)
    )).scalars())
    acl_rows = list((await session.execute(
        select(ResourceAcl).where(ResourceAcl.tenant_id == tenant_id)
    )).scalars())
    audit_rows = list((await session.execute(
        select(FinancialAuditLog).where(FinancialAuditLog.tenant_id == tenant_id)
    )).scalars())
    auth_rows = list((await session.execute(
        select(AuthAuditLog).where(AuthAuditLog.tenant_id == tenant_id)
    )).scalars())
    events = list((await session.execute(
        select(RunEvent).where(RunEvent.tenant_id == tenant_id).order_by(RunEvent.created_at.desc()).limit(500)
    )).scalars())

    tenant_row = (await session.execute(select(Tenant).where(Tenant.tenant_id == tenant_id))).scalar_one_or_none()
    tenants = [{
        "tenant_id": tenant_id,
        "name": tenant_row.display_name if tenant_row else tenant_id,
        "status": tenant_row.status if tenant_row else "UNKNOWN",
        "users": 0,
        "created_at": tenant_row.created_at.isoformat() if tenant_row and tenant_row.created_at else None,
    }]

    membership_rows = (await session.execute(
        select(TenantMembership, User)
        .join(User, User.user_id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
    )).all()
    user_roles: dict[str, set[str]] = {}
    user_permissions: dict[str, set[str]] = {}
    role_rows = (await session.execute(
        select(UserRole.user_id, Role.name, RolePermission.permission_id)
        .join(Role, Role.role_id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.role_id)
        .where(UserRole.tenant_id == tenant_id)
    )).all()
    for user_id, role_name, permission in role_rows:
        user_roles.setdefault(user_id, set()).add(role_name)
        user_permissions.setdefault(user_id, set()).add(permission)

    users = []
    run_activity: dict[str, str] = {}
    for run in runs:
        run_activity[run.user_id] = max(run_activity.get(run.user_id, ""), run.updated_at.isoformat() if run.updated_at else "")
    for membership, user in membership_rows:
        users.append({
            "user_id": user.user_id,
            "tenant_id": tenant_id,
            "display_name": user.display_name,
            "email": user.email,
            "roles": sorted(user_roles.get(user.user_id, set())),
            "delegated_identity": f"delegate-{user.user_id}",
            "status": membership.status,
            "last_active_at": run_activity.get(user.user_id),
            "organization_id": membership.organization_id,
        })
    tenants[0]["users"] = len(users)

    role_definitions = (await session.execute(
        select(Role.role_id, Role.name, Role.description, RolePermission.permission_id)
        .join(RolePermission, RolePermission.role_id == Role.role_id, isouter=True)
        .order_by(Role.role_id)
    )).all()
    roles_map: dict[str, dict] = {}
    for role_id, name, description, permission_id in role_definitions:
        item = roles_map.setdefault(role_id, {"role_id": role_id, "name": name, "description": description,
                                              "permissions": [], "member_count": 0})
        if permission_id:
            item["permissions"].append(permission_id)
    for user in users:
        for role_name in user["roles"]:
            for item in roles_map.values():
                if item["name"] == role_name:
                    item["member_count"] += 1

    acl = [{
        "resource": f"{grant.resource_type}:{grant.resource_id}",
        "subject": grant.user_id,
        "permission": grant.permission,
        "effect": "ALLOWED",
        "source": "FinancialResourceGrant",
    } for grant in grants]
    acl.extend({
        "resource": f"{row.resource_type}:{row.resource_id}",
        "subject": row.user_id,
        "permission": row.permission,
        "effect": "ALLOWED",
        "source": "ResourceAcl",
    } for row in acl_rows)
    acl.append({"resource": "*", "subject": "cross-tenant", "permission": "read", "effect": "DENIED", "source": "TenantBoundary"})

    abac_policies = [
        {"policy_id": "abac-tenant", "name": "同租户资源访问", "effect": "ALLOWED",
         "conditions": "resource.tenant == principal.tenant", "status": "ACTIVE", "source": "authorization.evaluate"},
        {"policy_id": "abac-default-deny", "name": "默认拒绝", "effect": "DENIED",
         "conditions": "no matching permission or ACL grant", "status": "ACTIVE", "source": "authorization.evaluate"},
    ]

    run_map = {str(run.run_id): run for run in runs}
    approvals = []
    security_events = []
    for event in events:
        payload = event.payload or {}
        if event.event_type == "APPROVAL_DECISION":
            run = run_map.get(str(event.run_id))
            approvals.append({
                "approval_id": str(event.event_id), "run_id": str(event.run_id),
                "requester": run.user_id if run else "unknown",
                "approver": payload.get("approver_user_id", "unknown"),
                "decision": "APPROVED" if payload.get("approved") else "REJECTED",
                "reason": payload.get("reason") or "",
                "time": event.created_at.isoformat() if event.created_at else None,
            })
        if event.event_type in {"TOOL_DENIED", "APPROVAL_DENIED", "RUN_FAILED", "TOOL_SUCCEEDED", "APPROVAL_GRANTED", "APPROVAL_DECISION"}:
            denied = event.event_type in {"TOOL_DENIED", "APPROVAL_DENIED", "RUN_FAILED"}
            run = run_map.get(str(event.run_id))
            security_events.append({
                "event_id": str(event.event_id),
                "type": "CROSS_TENANT_BLOCK" if event.event_type == "TOOL_DENIED" and "tenant" in str(payload).lower() else event.event_type,
                "actor": run.user_id if run else (payload.get("approver_user_id") or "unknown"),
                "tenant_id": event.tenant_id,
                "target": str(payload.get("tool") or event.run_id),
                "reason": str(payload.get("reason") or payload.get("error") or event.event_type),
                "time": event.created_at.isoformat() if event.created_at else None,
                "decision": "DENIED" if denied else "ALLOWED",
            })
    for row in auth_rows:
        security_events.append({
            "event_id": str(row.audit_id),
            "type": row.action,
            "actor": row.actor_user_id,
            "tenant_id": row.tenant_id,
            "target": row.resource_id,
            "reason": row.reason or row.outcome,
            "time": row.created_at.isoformat() if row.created_at else None,
            "decision": row.outcome,
        })

    delegated = [{
        "identity_id": f"delegate-{user['user_id']}",
        "name": f"{user['display_name']} delegated identity",
        "owner": user["user_id"],
        "tenant_id": tenant_id,
        "roles": user["roles"],
        "allowed_tools": [],
        "scopes": sorted(user_permissions.get(user["user_id"], set())),
        "expires_at": None,
        "status": user["status"],
    } for user in users]

    return {
        "tenants": tenants,
        "users": users,
        "roles": list(roles_map.values()),
        "abacPolicies": abac_policies,
        "acl": acl,
        "delegatedIdentities": delegated,
        "approvals": approvals,
        "events": security_events,
        "auditCount": len(audit_rows),
    }
