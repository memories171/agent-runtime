from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import math
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import can_access_run, can_cancel_run, can_manage_run, get_principal
from agent_runtime.auth_router import router as auth_router
from agent_runtime.benchmark_api import router as benchmark_router
from agent_runtime.chat_api import router as chat_router
from agent_runtime.config import settings
from agent_runtime.knowledge_api import router as knowledge_router
from agent_runtime.contracts import (
    ApprovalRequest,
    MemoryCreate,
    MemoryRead,
    Principal,
    RestoreRequest,
    RunCreate,
    RunEventRead,
    RunRead,
    UserInput,
    WorkflowInput,
)
from agent_runtime.db import (
    RunEvent,
    RunRecord,
    FinancialReviewCase,
    FinancialAuditLog,
    FinancialResourceGrant,
    RunTelemetryRecord,
    WorkflowOutbox,
    append_event,
    engine,
    fetch_run,
    get_session,
    init_db,
    session_for_tenant,
)
from agent_runtime.memory.service import create_memory, search_memories, to_memory_read
from agent_runtime.platform_api import router as platform_router
from agent_runtime.context_api import router as context_router
from agent_runtime.graph_api import router as graph_router
from agent_runtime.monitoring_api import router as monitoring_router
from agent_runtime.skills.registry import SkillRegistry
from agent_runtime.temporal_client import connect_temporal


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime_config()
    if settings.auto_create_db:
        await init_db()
    app.state.temporal = await connect_temporal()
    from agent_runtime.tools.registry import configure_mcp_servers
    try:
        await configure_mcp_servers(settings.mcp_servers_json)
    except Exception:
        logging.exception("MCP server discovery failed at startup; API continues without MCP tools")
    yield
    await engine.dispose()


app = FastAPI(title="Financial Agent Runtime", version="0.2.0", description="金融机构内部研究、运营与合规辅助 Agent Runtime", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(platform_router)
app.include_router(auth_router)
app.include_router(context_router)
app.include_router(graph_router)
app.include_router(monitoring_router)
app.include_router(benchmark_router)
app.include_router(chat_router)
app.include_router(knowledge_router)

_AUDIT_EXCLUDED_PREFIXES = ("/auth", "/health", "/docs", "/openapi.json", "/redoc")


async def _write_work_audit(request: Request, status_code: int) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _AUDIT_EXCLUDED_PREFIXES):
        return
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return

    try:
        action = f"{request.method}_{path.strip('/').replace('/', '_')}"[:64]
        run_id = None
        if "/runs/" in path:
            candidate = path.split("/runs/", 1)[1].split("/", 1)[0]
            try:
                run_id = UUID(candidate)
            except ValueError:
                pass
        async with session_for_tenant(principal.tenant_id) as audit_session:
            audit_session.add(FinancialAuditLog(
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                run_id=run_id,
                action=action,
                resource_type="http_request",
                resource_id=path[:128],
                details={
                    "method": request.method,
                    "path": path,
                    "query": dict(request.query_params),
                    "status_code": status_code,
                },
            ))
            await audit_session.commit()
    except Exception:
        logging.getLogger(__name__).warning("work audit write failed", exc_info=True)


@app.middleware("http")
async def record_work_audit(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        await _write_work_audit(request, 500)
        raise
    await _write_work_audit(request, response.status_code)
    return response


def _run_read(
    run: RunRecord,
    telemetry: RunTelemetryRecord | None = None,
    latest_event: str | None = None,
    event_span_seconds: float | None = None,
) -> RunRead:
    metadata = run.metadata_json or {}
    created_dt = run.created_at or datetime.now(timezone.utc)
    updated_dt = run.updated_at or created_dt
    created = created_dt.isoformat()
    updated = updated_dt.isoformat()
    # 真实值优先来自 run_telemetry，其次 Run metadata，最后用创建/更新时间差推导。
    # 老的测试 Run 只写了 agent_runs.metadata={}，但 run_telemetry 有完整记录。
    def pick(key: str, attr: str):
        value = getattr(telemetry, attr) if telemetry else None
        if value not in (None, "", 0):
            return value
        return metadata.get(key)

    def as_iso(value):
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    duration = metadata.get("duration_seconds")
    # 只有真正在运行或已结束的任务才有"运行时长"：
    # - RUNNING：正在跑；
    # - 终态 SUCCEEDED/FAILED/CANCELLED/TIMED_OUT：跑完了，有完整跨度。
    # CREATED/QUEUED/PAUSED/WAITING_* 都未在跑，等待时间不算运行，时长为 0。
    TIMED_STATUSES = {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
    if run.status not in TIMED_STATUSES:
        duration = 0
    else:
        # metadata 里的 0 是历史写入的默认占位值，不是真实时长。
        # 没有 RUN_STARTED 事件的 Run 无法证明执行过，墙钟差值（updated-created）
        # 可能来自管理操作而非执行，不能作为运行时长，此时保持 0。
        has_execution_evidence = event_span_seconds is not None
        if (duration in (None, "", 0) or (isinstance(duration, (int, float)) and duration <= 0)) and has_execution_evidence:
            # 只信任事件时间线的首末执行事件跨度，不用 updated-created 或心跳猜测。
            duration = event_span_seconds
    return RunRead(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        session_id=run.session_id,
        agent_id=run.agent_id,
        prompt=run.prompt,
        status=run.status,
        pause_state=run.pause_state,
        version=run.version,
        result=run.result,
        error=run.error,
        created_at=created,
        updated_at=updated,
        model_name=pick("model_name", "model_name") or settings.model_name,
        harness_version=pick("harness_version", "harness_version") or settings.harness_version,
        duration_seconds=round(float(duration or 0), 3),
        heartbeat_at=as_iso(pick("heartbeat_at", "heartbeat_at")),
        deadline_at=as_iso(pick("deadline_at", "deadline_at")),
        progress_pct=pick("progress_pct", "progress_pct") or 0,
        current_step=as_iso(pick("current_step", "current_step")),
        token_input=pick("token_input", "token_input") or 0,
        token_output=pick("token_output", "token_output") or 0,
        estimated_cost_usd=pick("estimated_cost_usd", "estimated_cost_usd") or 0.0,
        latest_event=as_iso(latest_event or pick("current_step", "current_step") or run.status),
    )


async def _get_owned_run(
    session: AsyncSession, run_id: UUID, principal: Principal, access_mode: str = "read"
) -> RunRecord:
    run = await fetch_run(session, run_id, principal.tenant_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(status_code=404, detail="run not found")
    if access_mode == "read":
        allowed = can_access_run(principal, run.user_id)
    elif access_mode == "manage":
        allowed = can_manage_run(principal, run.user_id)
    elif access_mode == "cancel":
        allowed = can_cancel_run(principal, run.user_id)
    elif access_mode == "approve":
        allowed = principal.user_id == run.user_id or "approval:approve" in principal.scopes
    else:
        raise ValueError(f"unsupported access mode: {access_mode}")
    if not allowed:
        # Do not reveal whether another user's Run exists in this tenant.
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/health")
async def health():
    return {"status": "ok", "service": "financial-agent-runtime"}


@app.get("/meta")
async def meta():
    return {
        "service": "financial-agent-runtime",
        "harness_version": settings.harness_version,
        "default_model": settings.model_name,
        "task_queue": settings.temporal_task_queue,
    }


@app.get("/runs", response_model=list[RunRead])
async def list_runs(
    limit: int = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(RunRecord).where(
        RunRecord.tenant_id == principal.tenant_id,
        RunRecord.deleted_at.is_(None),
    )
    if "runs:read:tenant" not in principal.scopes:
        stmt = stmt.where(RunRecord.user_id == principal.user_id)
    rows = list((await session.execute(
        stmt.order_by(desc(RunRecord.created_at)).limit(max(1, min(limit, 100)))
    )).scalars())
    if not rows:
        return []
    run_ids = [row.run_id for row in rows]
    telemetry_rows = (await session.execute(
        select(RunTelemetryRecord).where(RunTelemetryRecord.run_id.in_(run_ids))
    )).scalars()
    telemetry_by_run = {item.run_id: item for item in telemetry_rows}
    # 每个 Run 最近一条事件类型，前端用它作为"事件描述"替代裸 UUID。
    event_rows = (await session.execute(
        select(RunEvent.run_id, RunEvent.event_type, RunEvent.created_at)
        .where(RunEvent.run_id.in_(run_ids))
        .order_by(RunEvent.created_at.desc())
    )).all()
    latest_event_by_run: dict = {}
    # 执行时长只由"执行类"事件构成：RUN_STARTED 为起点，TOOL/LLM/CHECKPOINT/CONTEXT
    # 等为执行证据，终态为终点。审批等待、Worker 故障/恢复、删除等控制事件不算执行时间。
    exec_types = {
        "RUN_STARTED", "RUN_SUCCEEDED", "RUN_FAILED", "RUN_CANCELLED", "RUN_TIMED_OUT",
        "TOOL_INTENT_CREATED", "TOOL_SUCCEEDED", "TOOL_FAILED", "TOOL_DENIED",
        "TOOL_OBSERVED", "TOOL_RETRY_DISPATCHED", "TOOL_EXECUTION_CONFIRMED",
        "TOOL_OUTCOME_UNKNOWN", "TOOL_RECONCILED", "TOOL_RECONCILIATION_REQUIRED",
        "TOOL_IDEMPOTENT_REDISPATCH", "TOOL_ARGUMENT_CONFLICT",
        "CHECKPOINT_CREATED", "CHECKPOINT_SAVED", "CHECKPOINT_FAILED",
        "CONTEXT_PACK_BUILT", "CONTEXT_PROJECTED", "CHILD_CONTEXT_CREATED",
    }
    terminal_types = {"RUN_SUCCEEDED", "RUN_FAILED", "RUN_CANCELLED", "RUN_TIMED_OUT"}
    started_by_run: dict = {}
    last_exec_by_run: dict = {}
    terminal_by_run: dict = {}
    for run_id, event_type, event_time in event_rows:
        if run_id not in latest_event_by_run:
            latest_event_by_run[run_id] = event_type
        if event_type not in exec_types:
            continue
        if event_type == "RUN_STARTED":
            started_by_run[run_id] = event_time  # 倒序，最后覆盖到的是最早的 STARTED
        else:
            last_exec_by_run.setdefault(run_id, event_time)  # 倒序，首个即最近的执行事件
        if event_type in terminal_types:
            terminal_by_run[run_id] = event_time
    event_span_seconds = {}
    for run_id, started in started_by_run.items():
        end = terminal_by_run.get(run_id) or last_exec_by_run.get(run_id)
        if end and end >= started:
            event_span_seconds[run_id] = (end - started).total_seconds()
    return [
        _run_read(
            row,
            telemetry_by_run.get(row.run_id),
            latest_event_by_run.get(row.run_id),
            event_span_seconds.get(row.run_id),
        )
        for row in rows
    ]


MAX_STEPS_HARD_LIMIT = 200


def resolve_step_budget(
    duration_seconds: int,
    requested_steps: int | None,
    default_steps: int,
    seconds_per_step: int,
) -> tuple[int, dict[str, object] | None]:
    """Derive a safe step budget from the task duration.

    - required = ceil(duration_seconds / seconds_per_step)
    - effective = max(requested-or-default, required), capped at the 200 hard limit
    - required > 200 => ValueError, mapped to HTTP 400 by the API layer
    Returns (effective_steps, adjustment_record) where the record is non-None only
    when the value was raised, carrying original/adjusted/reason for audit.
    """
    if seconds_per_step <= 0:
        raise ValueError("estimated_seconds_per_step must be positive")
    required = math.ceil(duration_seconds / seconds_per_step)
    if required > MAX_STEPS_HARD_LIMIT:
        raise ValueError(
            f"duration {duration_seconds}s requires ~{required} steps at "
            f"{seconds_per_step}s/step, exceeding the hard limit of {MAX_STEPS_HARD_LIMIT}"
        )
    original = requested_steps if requested_steps is not None else default_steps
    effective = min(MAX_STEPS_HARD_LIMIT, max(original, required))
    if effective == original:
        return effective, None
    return effective, {
        "original": original,
        "adjusted": effective,
        "reason": "duration_budget",
        "duration_seconds": duration_seconds,
        "required_steps": required,
    }


@app.post("/runs", response_model=RunRead, status_code=202)
async def create_run(
    body: RunCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if "agent:execute" not in principal.scopes:
        raise HTTPException(status_code=403, detail="agent:execute scope required")
    run_id = uuid4()
    session_id = body.session_id or str(uuid4())
    run = RunRecord(
        run_id=run_id,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
        agent_id=body.agent_id,
        prompt=body.prompt,
        status="QUEUED",
        metadata_json={
            **body.metadata,
            "model_name": body.model_name or settings.model_name,
            "harness_version": settings.harness_version,
            "orchestration": body.orchestration or {},
        },
    )
    if body.graph and not settings.graph_enabled:
        raise HTTPException(status_code=403, detail="task graph is disabled by server configuration")
    session.add(run)
    await session.flush()
    max_duration = body.max_duration_seconds or settings.max_run_duration_seconds
    try:
        max_steps, budget_adjustment = resolve_step_budget(
            duration_seconds=max_duration,
            requested_steps=body.max_steps,
            default_steps=settings.max_agent_steps,
            seconds_per_step=settings.estimated_seconds_per_step,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run.metadata_json = {
        **run.metadata_json,
        "max_duration_seconds": max_duration,
        "max_steps": max_steps,
        "budget_adjustment": budget_adjustment,
    }
    workflow_input = WorkflowInput(
        run_id=str(run_id),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
        agent_id=body.agent_id,
        prompt=body.prompt,
        scopes=sorted(principal.scopes),
        organization_id=principal.organization_id,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        authentication_method=principal.authentication_method,
        token_expires_at=principal.token_expires_at,
        max_steps=max_steps,
        max_duration_seconds=max_duration,
        skill_ids=body.skill_ids,
        model_name=body.model_name or settings.model_name,
        harness_version=settings.harness_version,
        context_config=dict((body.metadata or {}).get("context_config") or {}),
        graph_config=dict(body.graph or {}),
        orchestration_config=dict(body.orchestration or {}),
        orchestration_limits=_frozen_orchestration_limits(),
        child_cancel_grace_seconds=settings.orchestration_child_cancel_grace_seconds,
    )
    # The Run and dispatch intent commit atomically. Worker retries outbox delivery.
    session.add(
        WorkflowOutbox(
            run_id=run_id,
            tenant_id=principal.tenant_id,
            command_id=f"start:{run_id}",
            command_type="START_WORKFLOW",
            payload=workflow_input.model_dump(mode="json"),
            status="PENDING",
        )
    )
    await append_event(session, run_id, principal.tenant_id, "RUN_QUEUED", {"agent_id": body.agent_id})
    if budget_adjustment is not None:
        await append_event(
            session, run_id, principal.tenant_id, "STEP_BUDGET_ADJUSTED", budget_adjustment
        )
    response = _run_read(run)
    await session.commit()
    return response


@app.get("/runs/{run_id}", response_model=RunRead)
async def get_run(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    return _run_read(await _get_owned_run(session, run_id, principal))


@app.get("/runs/{run_id}/events", response_model=list[RunEventRead])
async def get_run_events(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    await _get_owned_run(session, run_id, principal)
    result = await session.execute(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.tenant_id == principal.tenant_id).order_by(RunEvent.created_at.asc())
    )
    return [
        RunEventRead(event_id=e.event_id, event_type=e.event_type, payload=e.payload, created_at=e.created_at.isoformat())
        for e in result.scalars()
    ]


async def _signal(
    run_id: UUID,
    session: AsyncSession,
    principal: Principal,
    signal_name: str,
    *args,
    allowed_statuses: set[str] | None = None,
):
    run = await _get_owned_run(session, run_id, principal, access_mode="manage")
    if allowed_statuses is not None and run.status not in allowed_statuses:
        raise HTTPException(status_code=409, detail=f"cannot {signal_name} a Run in {run.status}")
    handle = app.state.temporal.get_workflow_handle(f"agent-run/{principal.tenant_id}/{run_id}")
    try:
        await handle.signal(signal_name, *args)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"workflow signal failed: {type(exc).__name__}") from exc
    return {"run_id": str(run.run_id), "signal": signal_name, "accepted": True}


async def _workflow_missing(tenant_id: str, run_id: UUID) -> bool:
    """Temporal 中已无对应 workflow 时 signal 无法送达，这类 Run 直接落库生效。"""
    handle = app.state.temporal.get_workflow_handle(f"agent-run/{tenant_id}/{run_id}")
    try:
        await handle.describe()
        return False
    except Exception as exc:
        if "not found" in str(exc).lower():
            return True
        return False


def _frozen_orchestration_limits() -> dict:
    """Snapshot orchestration settings into WorkflowInput for replay safety."""
    return {
        "parent_cancel_grace_seconds": settings.orchestration_parent_cancel_grace_seconds,
        "child_cancel_grace_seconds": settings.orchestration_child_cancel_grace_seconds,
        "allowed_agent_roles": sorted(
            item.strip() for item in settings.orchestration_allowed_agent_roles.split(",") if item.strip()
        ),
        "max_depth": settings.orchestration_max_depth,
    }


async def _require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(status_code=403, detail=f"{scope} scope required")


async def _active_membership(session: AsyncSession, tenant_id: str, user_id: str) -> bool:
    from agent_runtime.db import TenantMembership
    m = (await session.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
        )
    )).scalar_one_or_none()
    return m is not None


@app.post("/runs/{run_id}/pause")
async def pause_run(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    await _require_scope(principal, "run:pause")
    run = await _get_owned_run(session, run_id, principal, access_mode="manage")
    if run.status != "RUNNING":
        raise HTTPException(status_code=409, detail=f"cannot pause a Run in {run.status}")
    if await _workflow_missing(principal.tenant_id, run_id):
        outcome = await session.execute(
            update(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.tenant_id == principal.tenant_id,
                RunRecord.version == run.version, RunRecord.status == "RUNNING",
            ).values(status="PAUSED", pause_state="PAUSED", version=run.version + 1, updated_at=datetime.now(timezone.utc))
        )
        if outcome.rowcount != 1:
            raise HTTPException(status_code=409, detail="concurrent pause/resume conflict")
        await append_event(session, run_id, principal.tenant_id, "RUN_PAUSED", {"mode": "workflow_missing", "actor": principal.user_id})
        await session.commit()
        return {"run_id": str(run_id), "pause_state": "PAUSED", "accepted": True, "forced": True}
    if run.pause_state not in {"NONE", "RESUMING"}:
        raise HTTPException(status_code=409, detail=f"pause already requested (pause_state={run.pause_state})")
    from agent_runtime.outbox.commands import enqueue_command
    command_id = f"pause:{run_id}:{run.version}"
    outcome = await session.execute(
        update(RunRecord).where(
            RunRecord.run_id == run_id, RunRecord.tenant_id == principal.tenant_id,
            RunRecord.version == run.version, RunRecord.status == "RUNNING",
        ).values(pause_state="REQUESTED", version=RunRecord.version + 1)
    )
    if outcome.rowcount != 1:
        raise HTTPException(status_code=409, detail="concurrent pause/resume conflict")
    await enqueue_command(session, run_id=run_id, tenant_id=principal.tenant_id,
                           command_type="PAUSE_WORKFLOW", command_id=command_id,
                           payload={"requested_by": principal.user_id, "expected_version": run.version})
    await append_event(session, run_id, principal.tenant_id, "PAUSE_REQUESTED", {"command_id": command_id})
    await session.commit()
    return {"run_id": str(run_id), "pause_state": "REQUESTED", "accepted": True}


@app.post("/runs/{run_id}/resume")
async def resume_run(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    await _require_scope(principal, "run:resume")
    if not await _active_membership(session, principal.tenant_id, principal.user_id):
        raise HTTPException(status_code=403, detail="no active tenant membership")
    run = await _get_owned_run(session, run_id, principal, access_mode="manage")
    if run.status != "PAUSED":
        raise HTTPException(status_code=409, detail=f"cannot resume a Run in {run.status}")
    if await _workflow_missing(principal.tenant_id, run_id):
        outcome = await session.execute(
            update(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.tenant_id == principal.tenant_id,
                RunRecord.version == run.version, RunRecord.status == "PAUSED",
            ).values(status="RUNNING", pause_state="NONE", version=run.version + 1, updated_at=datetime.now(timezone.utc))
        )
        if outcome.rowcount != 1:
            raise HTTPException(status_code=409, detail="concurrent pause/resume conflict")
        await append_event(session, run_id, principal.tenant_id, "RUN_RESUMED", {"mode": "workflow_missing", "actor": principal.user_id})
        await session.commit()
        return {"run_id": str(run_id), "pause_state": "NONE", "accepted": True, "forced": True}
    if run.pause_state not in {"PAUSED", "PAUSING"}:
        raise HTTPException(status_code=409, detail=f"run is not in a resumable pause state ({run.pause_state})")
    # Verify checkpoint integrity before resuming.
    from agent_runtime.checkpoint.service import CheckpointSnapshotService
    from agent_runtime.checkpoint.storage import build_snapshot_store
    from agent_runtime.db import CheckpointRecord
    service = CheckpointSnapshotService(build_snapshot_store(settings), settings.snapshot_encryption_keys, settings.snapshot_encryption_key_id)
    try:
        await service.load(session, CheckpointRecord, tenant_id=principal.tenant_id, run_id=str(run_id))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"checkpoint integrity check failed: {type(exc).__name__}") from exc
    from agent_runtime.outbox.commands import enqueue_command
    command_id = f"resume:{run_id}:{run.version}"
    outcome = await session.execute(
        update(RunRecord).where(
            RunRecord.run_id == run_id, RunRecord.tenant_id == principal.tenant_id,
            RunRecord.version == run.version, RunRecord.status == "PAUSED",
        ).values(pause_state="RESUME_REQUESTED", version=RunRecord.version + 1)
    )
    if outcome.rowcount != 1:
        raise HTTPException(status_code=409, detail="concurrent pause/resume conflict")
    await enqueue_command(session, run_id=run_id, tenant_id=principal.tenant_id,
                           command_type="RESUME_WORKFLOW", command_id=command_id,
                           payload={"requested_by": principal.user_id, "expected_version": run.version})
    await append_event(session, run_id, principal.tenant_id, "RESUME_REQUESTED", {"command_id": command_id})
    await session.commit()
    return {"run_id": str(run_id), "pause_state": "RESUME_REQUESTED", "accepted": True}


@app.post("/runs/{run_id}/restore", response_model=RunRead, status_code=202)
async def restore_run(
    run_id: UUID,
    body: RestoreRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """B7 — start a NEW Run seeded from a verified checkpoint of ``run_id``.

    The original Run and its Temporal history are never modified. The new Run
    carries an immutable, versioned RestoreContract in its WorkflowInput.
    """
    from agent_runtime.checkpoint.restore_contract import RESTORE_CONTRACT_VERSION, build_restore_contract
    from agent_runtime.checkpoint.service import CheckpointSnapshotService
    from agent_runtime.checkpoint.storage import build_snapshot_store
    from agent_runtime.db import CheckpointRecord

    await _require_scope(principal, "run:restore")
    if "agent:execute" not in principal.scopes:
        raise HTTPException(status_code=403, detail="agent:execute scope required")
    if not await _active_membership(session, principal.tenant_id, principal.user_id):
        raise HTTPException(status_code=403, detail="no active tenant membership")

    # Ownership/ACL on the SOURCE run; manage mode keeps tenant isolation.
    source = await _get_owned_run(session, run_id, principal, access_mode="manage")

    service = CheckpointSnapshotService(
        build_snapshot_store(settings),
        settings.snapshot_encryption_keys,
        settings.snapshot_encryption_key_id,
    )
    try:
        envelope, snapshot = await service.load(
            session,
            CheckpointRecord,
            tenant_id=principal.tenant_id,
            run_id=str(run_id),
            checkpoint_id=body.checkpoint_id,
            sequence=body.checkpoint_sequence,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"checkpoint load failed: {type(exc).__name__}") from exc

    # tenant/ACL/capability consistency: a snapshot from another tenant or a
    # different principal identity must never be restored.
    snap_principal = snapshot.principal or {}
    if snap_principal.get("tenant_id") and snap_principal["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=403, detail="checkpoint tenant mismatch")
    if envelope.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="checkpoint envelope tenant mismatch")

    contract = build_restore_contract(
        envelope=envelope,
        state=snapshot,
        source_run_id=str(run_id),
        restored_by_user_id=principal.user_id,
        capability_token_id=snap_principal.get("capability_token_id"),
    )

    new_run_id = uuid4()
    session_id = source.session_id or str(uuid4())
    agent_id = body.agent_id or source.agent_id
    max_duration = source.metadata_json.get("max_duration_seconds") or settings.max_run_duration_seconds
    try:
        max_steps, _ = resolve_step_budget(
            duration_seconds=max_duration,
            requested_steps=source.metadata_json.get("max_steps"),
            default_steps=settings.max_agent_steps,
            seconds_per_step=settings.estimated_seconds_per_step,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    restored_run = RunRecord(
        run_id=new_run_id,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
        agent_id=agent_id,
        prompt=source.prompt,
        status="QUEUED",
        metadata_json={
            **source.metadata_json,
            "restore_contract_version": RESTORE_CONTRACT_VERSION,
            "restored_from_run_id": str(run_id),
            "restored_from_checkpoint_id": envelope.checkpoint_id,
            "recovery_source": "checkpoint_restore",
            "restore_reason": body.reason,
        },
    )
    session.add(restored_run)
    await session.flush()

    workflow_input = WorkflowInput(
        run_id=str(new_run_id),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
        agent_id=agent_id,
        prompt=source.prompt,
        scopes=sorted(principal.scopes),
        organization_id=principal.organization_id,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        authentication_method=principal.authentication_method,
        token_expires_at=principal.token_expires_at,
        max_steps=max_steps,
        max_duration_seconds=max_duration,
        skill_ids=[],
        model_name=source.metadata_json.get("model_name") or settings.model_name,
        harness_version=settings.harness_version,
        context_config=dict((source.metadata_json or {}).get("context_config") or {}),
        graph_config=dict((source.metadata_json or {}).get("graph") or {}),
        orchestration_config=dict((source.metadata_json or {}).get("orchestration") or {}),
        orchestration_limits=_frozen_orchestration_limits(),
        child_cancel_grace_seconds=settings.orchestration_child_cancel_grace_seconds,
        restore_contract=contract.model_dump(mode="json"),
    )
    session.add(
        WorkflowOutbox(
            run_id=new_run_id,
            tenant_id=principal.tenant_id,
            command_id=f"restore:{new_run_id}",
            command_type="START_WORKFLOW",
            payload=workflow_input.model_dump(mode="json"),
            status="PENDING",
        )
    )
    await append_event(session, new_run_id, principal.tenant_id, "RESTORE_REQUESTED", {
        "source_run_id": str(run_id),
        "checkpoint_id": envelope.checkpoint_id,
        "recovery_source": "checkpoint_restore",
        "restore_contract_version": RESTORE_CONTRACT_VERSION,
    })
    await session.commit()
    return restored_run


async def _cancel_pre_dispatch(
    session: AsyncSession, run: RunRecord, run_id: UUID, principal: Principal, mode: str
) -> None:
    # Lock both the Run and its outbox commands in one transaction, so a
    # dispatcher cannot start the workflow after seeing a pre-cancel snapshot.
    run = (await session.execute(
        select(RunRecord).where(RunRecord.run_id == run_id).with_for_update()
    )).scalar_one_or_none()
    if run is None:
        return
    outboxes = (await session.execute(
        select(WorkflowOutbox).where(
            WorkflowOutbox.run_id == run_id,
            WorkflowOutbox.status.in_(["PENDING", "DISPATCHING"]),
        ).with_for_update()
    )).scalars()
    for outbox in outboxes:
        outbox.status = "CANCELLED"
        outbox.dispatched_at = datetime.now(timezone.utc)
    await append_event(session, run_id, principal.tenant_id, "RUN_CANCELLED", {"mode": mode, "actor": principal.user_id})
    run.status = "CANCELLED"


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    run = await _get_owned_run(session, run_id, principal, access_mode="cancel")
    if run.status in {"CREATED", "QUEUED"}:
        await _cancel_pre_dispatch(session, run, run_id, principal, "pre_dispatch")
        await session.commit()
        return {"run_id": str(run_id), "signal": "cancel", "accepted": True}
    if await _workflow_missing(principal.tenant_id, run_id):
        await _cancel_pre_dispatch(session, run, run_id, principal, "workflow_missing")
        await session.commit()
        return {"run_id": str(run_id), "signal": "cancel", "accepted": True, "forced": True}
    return await _signal(run_id, session, principal, "cancel", allowed_statuses={"RUNNING", "PAUSED", "WAITING_INPUT", "WAITING_APPROVAL"})


@app.delete("/runs/{run_id}")
async def delete_run(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    if not ({"run:delete", "runs:manage:tenant"} & principal.scopes):
        raise HTTPException(status_code=403, detail="run:delete scope required")
    run = await _get_owned_run(session, run_id, principal, access_mode="manage")
    if run.status in {"RUNNING", "PAUSED", "WAITING_INPUT", "WAITING_APPROVAL"}:
        raise HTTPException(status_code=409, detail="cancel the run before deleting it")
    if run.status in {"CREATED", "QUEUED"}:
        await _cancel_pre_dispatch(session, run, run_id, principal, "pre_dispatch_deletion")
    run.deleted_at = datetime.now(timezone.utc)
    await append_event(session, run_id, principal.tenant_id, "RUN_DELETED", {"deleted_by": principal.user_id})
    await session.commit()
    return {"run_id": str(run_id), "deleted": True}


@app.post("/runs/{run_id}/input")
async def provide_input(run_id: UUID, body: UserInput, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    return await _signal(run_id, session, principal, "provide_input", body.value, allowed_statuses={"WAITING_INPUT"})


@app.post("/runs/{run_id}/approval")
async def provide_approval(run_id: UUID, body: ApprovalRequest, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    if "approval:approve" not in principal.scopes:
        raise HTTPException(status_code=403, detail="approval:approve scope required")
    run = await _get_owned_run(session, run_id, principal, access_mode="approve")
    if run.status != "WAITING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"Run is not waiting for approval (status={run.status})")
    required_event = await session.execute(
        select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.tenant_id == principal.tenant_id,
            RunEvent.event_type == "APPROVAL_REQUIRED",
        ).order_by(desc(RunEvent.created_at)).limit(1)
    )
    approval_context = required_event.scalar_one_or_none()
    tool_name = (approval_context.payload or {}).get("tool") if approval_context else None
    await _enforce_financial_approval_policy(
        session, principal,
        run_id=run_id,
        tool_name=tool_name,
        requester_user_id=run.user_id,
    )
    payload = {"approved": body.approved, "reason": body.reason, "approver_user_id": principal.user_id,
               "approved_at": datetime.now(timezone.utc).isoformat(), "tool": tool_name}
    # 审批决策走状态机（条件更新/幂等/过期），与 Outbox 记录同事务提交。
    from agent_runtime.approval.service import ApprovalError, ApprovalService, ApprovalStateChangedError
    svc = ApprovalService()
    approval_id = (approval_context.payload or {}).get("approval_id") if approval_context else None
    if approval_id:
        try:
            await svc.decide(session, tenant_id=principal.tenant_id, approval_id=approval_id,
                             approved=body.approved, approver_id=principal.user_id, reason=body.reason)
        except ApprovalStateChangedError as exc:
            await session.commit()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except ApprovalError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    from agent_runtime.outbox.commands import enqueue_command
    command_id = f"approval:{run_id}:{approval_id or tool_name}:{principal.user_id}:{body.approved}"
    await enqueue_command(session, run_id=run_id, tenant_id=principal.tenant_id,
                           command_type="APPROVAL_DECISION", command_id=command_id,
                           payload={**payload, "approval_id": approval_id, "tool_call_id": (approval_context.payload or {}).get("tool_call_id")})
    await session.commit()
    return {"run_id": str(run.run_id), "accepted": True, "approval_id": approval_id,
            "note": "approval decision committed; signal delivered via outbox"}


@app.post("/memory", response_model=MemoryRead, status_code=201)
async def add_memory(body: MemoryCreate, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    if "memory:write" not in principal.scopes:
        raise HTTPException(status_code=403, detail="memory:write scope required")
    try:
        record = await create_memory(
            session, principal, body.content, body.scope, body.memory_type, body.source_run_id,
            metadata=body.metadata, title=body.title, confidence=body.confidence,
            expires_at=body.expires_at, acl=body.acl,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return to_memory_read(record)


@app.get("/memory/search", response_model=list[MemoryRead])
async def memory_search(q: str, limit: int = 10, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    if "memory:read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="memory:read scope required")
    rows = await search_memories(session, principal, q, max(1, min(limit, 50)))
    return [to_memory_read(row) for row in rows]


@app.get("/finance/review-cases")
async def list_financial_review_cases(
    status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if "financial:case:read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="financial:case:read scope required")
    stmt = select(FinancialReviewCase).where(FinancialReviewCase.tenant_id == principal.tenant_id)
    if "runs:read:tenant" not in principal.scopes and "financial:audit:read" not in principal.scopes:
        stmt = stmt.where(FinancialReviewCase.created_by == principal.user_id)
    if status:
        stmt = stmt.where(FinancialReviewCase.status == status.upper())
    rows = list((await session.execute(stmt.order_by(FinancialReviewCase.created_at.desc()).limit(max(1, min(limit, 100))))).scalars())
    return [{"case_id": str(row.case_id), "case_type": row.case_type, "subject_id": row.subject_id,
             "rationale": row.rationale, "evidence_transaction_ids": row.evidence_transaction_ids,
             "status": row.status, "created_by": row.created_by,
             "created_at": row.created_at.isoformat() if row.created_at else None,
             "reviewer": row.reviewer,
             "review_decision": row.review_decision,
             "review_reason": row.review_reason,
             "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
             "review_history": row.review_history or []} for row in rows]


@app.post("/finance/review-cases/{case_id}/actions")
async def act_on_financial_review_case(
    case_id: UUID,
    body: dict,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if "financial:case:review" not in principal.scopes:
        raise HTTPException(status_code=403, detail="financial:case:review scope required")
    action = str(body.get("action", "")).upper()
    reason = str(body.get("reason", "")).strip()
    status_map = {
        "START_REVIEW": "IN_REVIEW",
        "APPROVE": "APPROVED",
        "REJECT": "REJECTED",
        "ESCALATE": "ESCALATED",
        "ARCHIVE": "ARCHIVED",
    }
    if action not in status_map:
        raise HTTPException(status_code=400, detail="unsupported review action")
    if not reason:
        raise HTTPException(status_code=400, detail="review reason required")

    row = (await session.execute(
        select(FinancialReviewCase).where(
            FinancialReviewCase.tenant_id == principal.tenant_id,
            FinancialReviewCase.case_id == case_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="review case not found")
    if row.status in {"APPROVED", "REJECTED", "ARCHIVED"}:
        raise HTTPException(status_code=409, detail="review case already closed")

    allowed_transitions = {
        "START_REVIEW": {"OPEN"},
        "APPROVE": {"IN_REVIEW", "ESCALATED"},
        "REJECT": {"IN_REVIEW", "ESCALATED"},
        "ESCALATE": {"IN_REVIEW"},
        "ARCHIVE": {"APPROVED", "REJECTED", "ESCALATED"},
    }
    if row.status not in allowed_transitions[action]:
        raise HTTPException(status_code=409, detail=f"invalid review transition from {row.status}")

    # 双人复核与资源授权：复核人不能是案件发起人，且必须持有该案件主体的授权。
    if principal.user_id == row.created_by:
        raise HTTPException(status_code=403, detail="该金融操作要求双人复核：复核人不能是案件发起人")
    if "financial:case:approve" not in principal.scopes:
        raise HTTPException(status_code=403, detail="financial:case:approve scope required")
    resource_type = {
        "KYC": "customer", "AML": "transaction", "TRANSACTION_REVIEW": "account",
    }.get(row.case_type)
    if resource_type is None or not row.subject_id:
        raise HTTPException(status_code=422, detail=f"unsupported case type: {row.case_type}")
    grant = await session.execute(select(FinancialResourceGrant.grant_id).where(
        FinancialResourceGrant.tenant_id == principal.tenant_id,
        FinancialResourceGrant.user_id == principal.user_id,
        FinancialResourceGrant.resource_type.in_([resource_type, "*"]),
        FinancialResourceGrant.resource_id.in_([row.subject_id, "*"]),
        FinancialResourceGrant.permission.in_(["case:approve", "case:review", "*"]),
    ).limit(1))
    if grant.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="复核人没有该案件主体的资源授权")

    now = datetime.now(timezone.utc)
    row.status = status_map[action]
    row.reviewer = principal.user_id
    row.review_decision = action
    row.review_reason = reason
    row.reviewed_at = now
    history = list(row.review_history or [])
    history.append({
        "actor": principal.user_id,
        "action": action,
        "reason": reason,
        "status": row.status,
        "at": now.isoformat(),
    })
    row.review_history = history
    await session.commit()
    return {
        "case_id": str(row.case_id),
        "status": row.status,
        "reviewer": row.reviewer,
        "review_decision": row.review_decision,
        "review_reason": row.review_reason,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


@app.get("/finance/audit-logs")
async def list_financial_audit_logs(
    limit: int = 100,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if "financial:audit:read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="financial:audit:read scope required")
    rows = list((await session.execute(
        select(FinancialAuditLog).where(FinancialAuditLog.tenant_id == principal.tenant_id)
        .order_by(FinancialAuditLog.created_at.desc()).limit(max(1, min(limit, 500)))
    )).scalars())
    return [{"audit_id": str(row.audit_id), "actor_user_id": row.actor_user_id,
             "run_id": str(row.run_id) if row.run_id else None, "action": row.action,
             "resource_type": row.resource_type, "resource_id": row.resource_id,
             "details": row.details, "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in rows]


@app.get("/skills")
async def list_skills(principal: Principal = Depends(get_principal)):
    return SkillRegistry(settings.skills_dir).list_skills()


@app.get("/skills/{skill_id}")
async def get_skill(skill_id: str, principal: Principal = Depends(get_principal)):
    skill = SkillRegistry(settings.skills_dir).load_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return skill


@app.get("/tools")
async def list_tools(principal: Principal = Depends(get_principal)):
    from agent_runtime.tools.registry import registry
    return [
        {"name": t.name, "display_name": t.display_name, "description": t.description, "input_schema": t.input_schema, "required_scopes": sorted(t.required_scopes), "risk_level": t.risk_level, "idempotent": t.idempotent}
        for t in registry.list()
    ]


def _approval_read(record) -> dict[str, Any]:
    return {
        "approval_id": str(record.approval_id), "run_id": str(record.run_id),
        "tenant_id": record.tenant_id, "tool_call_id": record.tool_call_id,
        "tool_name": record.tool_name, "target_resource_id": record.target_resource_id,
        "operation_type": record.operation_type, "canonical_arguments": record.canonical_arguments,
        "canonical_arguments_hash": record.canonical_arguments_hash, "action_hash": record.action_hash,
        "requested_by": record.requested_by, "approver_id": record.approver_id,
        "policy_version": record.policy_version, "risk_level": record.risk_level,
        "nonce": record.nonce, "issued_at": record.issued_at.isoformat() if record.issued_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "approval_status": record.approval_status, "consumed_at": record.consumed_at.isoformat() if record.consumed_at else None,
        "reason": record.reason, "version": record.version,
    }


def _require_approval_reader(principal: Principal) -> None:
    read_scopes = {
        "approval:read", "approval:approve", "runs:manage:tenant",
        "audit:read", "audit:read:tenant", "financial:audit:read",
    }
    if not (read_scopes & principal.scopes):
        raise HTTPException(status_code=403, detail="approval read permission required")


async def _enforce_financial_approval_policy(
    session: AsyncSession,
    principal: Principal,
    *,
    run_id: UUID,
    tool_name: str | None,
    requester_user_id: str,
) -> None:
    """Shared dual-control check for high-risk financial tools.

    Both the run-scoped approval route and the approval-id routes call this so
    that a caller cannot bypass distinct-approver / resource-grant checks by
    choosing a different endpoint.
    """
    from agent_runtime.tools.registry import registry as tool_registry

    approved_tool = tool_registry.get(tool_name) if tool_name else None
    if not approved_tool or not approved_tool.requires_distinct_approver:
        return
    if principal.user_id == requester_user_id:
        raise HTTPException(
            status_code=403,
            detail="该金融操作要求双人复核：审批人不能是任务发起人",
        )
    if "financial:case:approve" not in principal.scopes:
        raise HTTPException(status_code=403, detail="financial:case:approve scope required")

    required_event = await session.execute(
        select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.tenant_id == principal.tenant_id,
            RunEvent.event_type == "APPROVAL_REQUIRED",
        ).order_by(desc(RunEvent.created_at)).limit(1)
    )
    context = required_event.scalar_one_or_none()
    payload = (context.payload or {}) if context else {}
    resource_type = payload.get("approval_resource_type")
    resource_id = payload.get("approval_resource_id") or payload.get("target_resource_id")
    if resource_type and resource_id:
        grant = await session.execute(select(FinancialResourceGrant.grant_id).where(
            FinancialResourceGrant.tenant_id == principal.tenant_id,
            FinancialResourceGrant.user_id == principal.user_id,
            FinancialResourceGrant.resource_type.in_([resource_type, "*"]),
            FinancialResourceGrant.resource_id.in_([resource_id, "*"]),
            FinancialResourceGrant.permission.in_(["case:approve", "*"]),
        ).limit(1))
        if grant.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="审批人没有该金融资源的案件审批授权")


@app.get("/runs/{run_id}/approvals")
async def list_run_approvals(run_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    from agent_runtime.db import ApprovalBinding
    await _get_owned_run(session, run_id, principal)
    rows = (await session.execute(
        select(ApprovalBinding).where(ApprovalBinding.tenant_id == principal.tenant_id, ApprovalBinding.run_id == run_id)
        .order_by(ApprovalBinding.created_at.desc())
    )).scalars()
    return [_approval_read(r) for r in rows]


@app.get("/approvals")
async def list_approvals(principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    from agent_runtime.db import ApprovalBinding
    _require_approval_reader(principal)
    rows = (await session.execute(
        select(ApprovalBinding).where(ApprovalBinding.tenant_id == principal.tenant_id)
        .order_by(ApprovalBinding.created_at.desc()).limit(200)
    )).scalars()
    return [_approval_read(r) for r in rows]


@app.get("/approvals/{approval_id}")
async def get_approval(approval_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    from agent_runtime.db import ApprovalBinding
    _require_approval_reader(principal)
    record = await session.get(ApprovalBinding, approval_id)
    if record is None or record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="approval not found")
    return _approval_read(record)


@app.get("/approvals/{approval_id}/action-envelope")
async def get_approval_action_envelope(approval_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    from agent_runtime.db import ApprovalBinding
    _require_approval_reader(principal)
    record = await session.get(ApprovalBinding, approval_id)
    if record is None or record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="approval not found")
    return {
        "approval_id": str(record.approval_id), "tenant_id": record.tenant_id, "run_id": str(record.run_id),
        "tool_call_id": record.tool_call_id, "tool_name": record.tool_name,
        "target_resource_id": record.target_resource_id, "operation_type": record.operation_type,
        "canonical_arguments": record.canonical_arguments,
        "canonical_arguments_hash": record.canonical_arguments_hash, "action_hash": record.action_hash,
        "requested_by": record.requested_by, "policy_version": record.policy_version,
        "risk_level": record.risk_level, "nonce": record.nonce,
        "issued_at": record.issued_at.isoformat() if record.issued_at else None,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "approval_status": record.approval_status, "signature_reference": record.signature_reference,
    }


@app.post("/approvals/{approval_id}/reject")
async def reject_approval(approval_id: UUID, body: ApprovalRequest, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    return await _decide_approval(approval_id, body, principal, session, approved=False)


@app.post("/approvals/{approval_id}/approve")
async def approve_by_id(approval_id: UUID, body: ApprovalRequest, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    return await _decide_approval(approval_id, body, principal, session, approved=True)


@app.post("/approvals/{approval_id}/revoke")
async def revoke_approval(approval_id: UUID, principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)):
    from agent_runtime.approval.service import ApprovalError, ApprovalService
    from agent_runtime.auth import can_manage_run
    from agent_runtime.db import ApprovalBinding, RunRecord
    await _require_scope(principal, "approval:approve")
    record = await session.get(ApprovalBinding, approval_id)
    if record is None or record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="approval not found")
    run = await session.get(RunRecord, record.run_id)
    if run is None or not can_manage_run(principal, run.user_id):
        raise HTTPException(status_code=403, detail="cannot revoke approval for this run")
    try:
        await ApprovalService().revoke(session, tenant_id=principal.tenant_id, approval_id=str(approval_id))
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await session.commit()
    return {"approval_id": str(approval_id), "revoked": True}


async def _decide_approval(approval_id: UUID, body: ApprovalRequest, principal: Principal, session: AsyncSession, approved: bool):
    from agent_runtime.approval.service import ApprovalError, ApprovalService, ApprovalStateChangedError
    from agent_runtime.auth import can_manage_run
    from agent_runtime.db import ApprovalBinding, RunRecord
    await _require_scope(principal, "approval:approve")
    record = await session.get(ApprovalBinding, approval_id)
    if record is None or record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="approval not found")
    run = await session.get(RunRecord, record.run_id)
    if run is None or not can_manage_run(principal, run.user_id):
        raise HTTPException(status_code=403, detail="cannot approve this run")
    if principal.user_id == record.requested_by:
        raise HTTPException(status_code=403, detail="requester cannot approve own high-risk operation")
    await _enforce_financial_approval_policy(
        session, principal,
        run_id=record.run_id,
        tool_name=record.tool_name,
        requester_user_id=record.requested_by,
    )
    try:
        await ApprovalService().decide(session, tenant_id=principal.tenant_id, approval_id=str(approval_id),
                                       approved=approved, approver_id=principal.user_id, reason=body.reason)
    except ApprovalStateChangedError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    from agent_runtime.outbox.commands import enqueue_command
    await enqueue_command(session, run_id=record.run_id, tenant_id=principal.tenant_id,
                           command_type="APPROVAL_DECISION",
                           command_id=f"approval:{record.run_id}:{approval_id}:{principal.user_id}:{approved}",
                           payload={"approval_id": str(approval_id), "run_id": str(record.run_id),
                                    "tool_call_id": record.tool_call_id, "approved": approved,
                                    "reason": body.reason, "approver_user_id": principal.user_id,
                                    "tool": record.tool_name})
    await session.commit()
    return {"approval_id": str(approval_id), "accepted": True, "approved": approved}
