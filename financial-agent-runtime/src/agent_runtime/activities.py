from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from temporalio import activity

from agent_runtime.agent.model import model_adapter
from agent_runtime.checkpoint.service import CheckpointSnapshotService
from agent_runtime.checkpoint.storage import build_snapshot_store
from agent_runtime.config import settings
from agent_runtime.recovery.invocations import ToolInvocationService
from agent_runtime.db import (
    CheckpointRecord, RunEvent, RunRecord, RunTelemetryRecord, SkillState, ToolInvocation,
    WorkerFailureRecord, append_event, session_for_tenant,
)
from agent_runtime.orchestration.token_service import CapabilityTokenService
from agent_runtime.tools.gateway import (
    ApprovalRequired, ToolDenied, attenuated_principal_data, execute_tool, make_idempotency_key,
)


_checkpoint_service = CheckpointSnapshotService(
    build_snapshot_store(settings),
    settings.snapshot_encryption_keys,
    settings.snapshot_encryption_key_id,
)


async def _run_with_heartbeats(awaitable, details: dict[str, Any], interval_seconds: int = 15):
    async def pulse() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            activity.heartbeat(details)

    task = asyncio.create_task(pulse())
    try:
        return await awaitable
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@activity.defn
async def persist_status_activity(payload: dict[str, Any]) -> None:
    async with session_for_tenant(payload.get("tenant_id")) as session:
        run = await session.get(RunRecord, UUID(payload["run_id"]))
        if run is None:
            return
        run.status = payload["status"]
        if "pause_state" in payload:
            run.pause_state = payload["pause_state"]
        run.version = RunRecord.version + 1
        if "result" in payload:
            run.result = payload["result"]
        if "error" in payload:
            run.error = payload["error"]
        await append_event(
            session,
            run.run_id,
            run.tenant_id,
            payload.get("event_type", "STATUS_CHANGED"),
            {k: v for k, v in payload.items() if k not in {"run_id", "status"}},
        )
        await session.commit()


@activity.defn
async def agent_decide_activity(state: dict[str, Any]) -> dict[str, Any]:
    activity.heartbeat({"stage": "model_decision", "run_id": state["run_id"]})
    decision = await _run_with_heartbeats(
        model_adapter.decide(state), {"stage": "model_decision", "run_id": state["run_id"]}
    )
    result = decision.model_dump(mode="json")
    # Provider-reported usage (if any) is returned explicitly; never fabricated upstream.
    result["_provider_usage"] = state.get("provider_usage") or {"reported": False}
    return result


@activity.defn
async def retrieve_memory_activity(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from agent_runtime.contracts import Principal
    from agent_runtime.memory.service import search_memories

    principal = Principal.model_validate(payload["principal"])
    if "memory:read" not in principal.scopes:
        return []
    async with session_for_tenant(principal.tenant_id) as session:
        rows = await search_memories(session, principal, payload["query"], limit=8)
        return [
            {"memory_id": str(row.memory_id), "scope": row.scope, "type": row.memory_type, "content": row.content}
            for row in rows
        ]


@activity.defn
async def load_skills_activity(skill_ids: list[str]) -> list[dict[str, str]]:
    from agent_runtime.config import settings
    from agent_runtime.skills.registry import SkillRegistry

    registry = SkillRegistry(settings.skills_dir)
    loaded = []
    async with session_for_tenant(None) as session:
        states = {row.skill_id: row.enabled for row in (await session.execute(select(SkillState))).scalars()}
    for skill_id in skill_ids:
        if not states.get(skill_id, True):
            continue
        skill = registry.load_skill(skill_id)
        if skill is not None:
            loaded.append(skill)
    return loaded


@activity.defn
async def authorize_tool_activity(payload: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.orchestration.token_service import CapabilityDenied
    from agent_runtime.tools.gateway import authorize_tool, authorize_tool_with_capability
    from agent_runtime.tools.registry import registry

    tool = registry.get(payload["tool_name"])
    if tool is None:
        return {"allowed": False, "reason": "tool_not_registered"}
    capability_token_id = payload.get("capability_token_id")
    try:
        if capability_token_id:
            await authorize_tool_with_capability(
                payload["principal"], payload["tenant_id"], tool, str(capability_token_id),
                approval_granted=False,
                resource_scope=payload.get("resource_scope"),
                subject_run_id=payload.get("subject_run_id") or payload.get("run_id"),
            )
        else:
            authorize_tool(payload["principal"], payload["tenant_id"], tool, approval_granted=False)
    except ApprovalRequired:
        return {"allowed": True, "requires_approval": True, "risk_level": tool.risk_level, "requires_distinct_approver": tool.requires_distinct_approver}
    except (ToolDenied, CapabilityDenied) as exc:
        from agent_runtime.authorization.audit import record_decision
        from agent_runtime.authorization.policies import AuthorizationDecision, AuthorizationRequest
        from agent_runtime.contracts import Principal

        principal = Principal.model_validate(payload["principal"])
        async with session_for_tenant(payload["tenant_id"]) as session:
            await record_decision(
                session,
                AuthorizationRequest(
                    principal=principal,
                    action="execute",
                    resource_type="tool",
                    resource_id=payload["tool_name"],
                    tenant_id=payload["tenant_id"],
                ),
                AuthorizationDecision.deny(str(exc)),
                action_label="TOOL_AUTHZ",
            )
            await session.commit()
        result = {"allowed": False, "reason": str(exc)}
        if isinstance(exc, CapabilityDenied):
            result.update({"error_class": "non_retryable", "code": exc.code})
        return result
    return {"allowed": True, "requires_approval": False, "risk_level": tool.risk_level, "requires_distinct_approver": tool.requires_distinct_approver}


@activity.defn
async def execute_tool_activity(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = payload["tool_name"]
    arguments = payload.get("arguments", {})
    key = payload["idempotency_key"]
    tenant_id = payload["tenant_id"]
    run_id = UUID(payload["run_id"])
    tool_call_id = str(payload.get("tool_call_id") or key)
    # Temporal Activity attempt is monotonic. If a human decision already fenced
    # the journal, the next attempt is shifted into the human namespace.
    attempt = int(activity.info().attempt)
    from agent_runtime.tools.registry import registry

    tool_definition = registry.get(tool_name)
    idempotent_provider = bool(tool_definition and tool_definition.idempotent)
    invocations = ToolInvocationService()

    principal_data = payload["principal"]
    capability_token_id = payload.get("capability_token_id")
    capability_kwargs: dict[str, Any] = {}
    if capability_token_id:
        from agent_runtime.contracts import Principal

        actor = Principal.model_validate(principal_data)
        capability_kwargs = {
            "required_capabilities": set(tool_definition.required_scopes) if tool_definition else set(),
            "resource_scope": payload.get("resource_scope"),
            "subject_run_id": payload.get("subject_run_id") or str(run_id),
            "actor": actor,
        }
        token = await CapabilityTokenService.validate(str(capability_token_id), tenant_id, **capability_kwargs)
        principal_data = attenuated_principal_data(principal_data, set(token.capabilities))

    # Intent-first: state machine + fencing decide reuse / execute / conflict / unknown.
    async with session_for_tenant(tenant_id) as session:
        fencing_token = await invocations.fencing_token_for_attempt(
            session, tenant_id=tenant_id, idempotency_key=key, attempt=attempt
        )
        lease_owner = f"{payload.get('lease_owner', 'worker')}#{fencing_token}"
        decision = await invocations.begin(
            session,
            run_id=run_id,
            tenant_id=tenant_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=key,
            fencing_token=fencing_token,
            lease_owner=lease_owner,
            idempotent_provider=idempotent_provider,
        )
    if decision["decision"] == "reuse":
        return decision["result"]

    # Re-check immediately before the side effect to narrow the revoke/authorize TOCTOU window.
    if capability_token_id:
        await CapabilityTokenService.validate(str(capability_token_id), tenant_id, **capability_kwargs)

    heartbeat_details = {
        "stage": "tool_execution", "tool": tool_name, "run_id": payload["run_id"],
        "idempotency_key": key, "fencing_token": fencing_token, "attempt": activity.info().attempt,
    }
    try:
        result = await _run_with_heartbeats(
            execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                principal_data=principal_data,
                tenant_id=tenant_id,
                approval_granted=bool(payload.get("approval_granted", False)),
                run_id=payload["run_id"],
                idempotency_key=key,
            ),
            heartbeat_details,
        )
    except (PermissionError, ToolDenied, ValueError) as exc:
        # Deterministic, provider-confirmed failures: safe to mark FAILED (retryable).
        async with session_for_tenant(tenant_id) as session:
            await invocations.mark_failed(
                session, tenant_id=tenant_id, idempotency_key=key,
                fencing_token=fencing_token, last_error=str(exc),
            )
            await session.commit()
        raise
    except Exception as exc:
        # Outcome uncertain (timeout / transport error): isolate as UNKNOWN, then
        # move it to RECONCILIATION_REQUIRED so humans can discover it. No terminal
        # outcome is inferred here — only a human may finalise via /reconcile.
        reason = type(exc).__name__ + ": " + str(exc)
        async with session_for_tenant(tenant_id) as session:
            await invocations.mark_unknown(
                session, tenant_id=tenant_id, idempotency_key=key,
                fencing_token=fencing_token, last_error=reason,
            )
            await invocations.require_reconciliation(
                session, tenant_id=tenant_id, idempotency_key=key,
                fencing_token=fencing_token, reason=reason,
            )
            await session.commit()
        raise
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    if len(encoded) > settings.max_tool_result_chars:
        preview_size = max(256, settings.max_tool_result_chars - 500)
        result = {"truncated": True, "preview": encoded[:preview_size]}

    async with session_for_tenant(tenant_id) as session:
        await invocations.confirm(
            session,
            tenant_id=tenant_id,
            idempotency_key=key,
            fencing_token=fencing_token,
            result=result,
            external_reference=(result.get("case_id") or result.get("external_id") if isinstance(result, dict) else None),
            result_reference=f"invocation/{key}",
        )
        await session.commit()
    return result


@activity.defn
async def persist_telemetry_activity(payload: dict[str, Any]) -> None:
    run_id = UUID(payload["run_id"])
    async with session_for_tenant(payload.get("tenant_id")) as session:
        result = await session.execute(select(RunTelemetryRecord).where(RunTelemetryRecord.run_id == run_id))
        record = result.scalar_one_or_none()
        if record is None:
            record = RunTelemetryRecord(run_id=run_id, tenant_id=payload["tenant_id"])
            session.add(record)
        record.model_name = payload.get("model_name")
        record.harness_version = payload.get("harness_version")
        record.prompt_version = payload.get("prompt_version")
        record.heartbeat_at = _parse_iso(payload.get("heartbeat_at"))
        record.deadline_at = _parse_iso(payload.get("deadline_at"))
        record.current_step = payload.get("current_step", "执行中")
        record.progress_pct = int(payload.get("progress_pct", 0))
        record.token_input = int(payload.get("token_input", 0))
        record.token_output = int(payload.get("token_output", 0))
        record.estimated_cost_usd = float(payload.get("estimated_cost_usd", 0.0))
        record.tool_calls_json = payload.get("tool_calls") or []
        record.llm_calls_json = payload.get("llm_calls") or []

        run = await session.get(RunRecord, run_id)
        if run is not None:
            metadata = dict(run.metadata_json or {})
            metadata.update({
                "model_name": record.model_name,
                "harness_version": record.harness_version,
                "duration_seconds": int(payload.get("duration_seconds", 0)),
                "heartbeat_at": payload.get("heartbeat_at"),
                "deadline_at": payload.get("deadline_at"),
                "progress_pct": record.progress_pct,
                "current_step": record.current_step,
                "token_input": record.token_input,
                "token_output": record.token_output,
                "estimated_cost_usd": record.estimated_cost_usd,
            })
            run.metadata_json = metadata
        await session.commit()


@activity.defn
async def persist_checkpoint_activity(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with session_for_tenant(payload.get("tenant_id")) as session:
            envelope = await _checkpoint_service.persist(
                session, CheckpointRecord, append_event, payload=payload,
            )
            return {
                "checkpoint_id": envelope.checkpoint_id,
                "state_digest": envelope.state_digest,
                "schema_version": envelope.schema_version,
                "persistence_status": envelope.persistence_status,
            }
    except Exception:
        try:
            async with session_for_tenant(payload.get("tenant_id")) as session:
                await append_event(
                    session, UUID(payload["run_id"]), payload["tenant_id"], "CHECKPOINT_FAILED",
                    {"checkpoint_id": payload["checkpoint_id"], "step": payload["step"]},
                )
                await session.commit()
        except Exception:
            pass
        raise


@activity.defn
async def persist_worker_failure_activity(payload: dict[str, Any]) -> None:
    async with session_for_tenant(payload.get("tenant_id")) as session:
        existing = await session.get(WorkerFailureRecord, UUID(payload["failure_id"]))
        if existing is not None:
            return
        record = WorkerFailureRecord(
            failure_id=UUID(payload["failure_id"]),
            run_id=UUID(payload["run_id"]),
            tenant_id=payload["tenant_id"],
            worker_id=payload.get("worker_id", "temporal-worker"),
            reason=payload["reason"],
            recovery_status="RECOVERING",
            recovery_count=int(payload.get("recovery_count", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            recovered_from_checkpoint_id=(UUID(payload["checkpoint_id"]) if payload.get("checkpoint_id") else None),
            recovery_position=payload.get("recovery_position", "workflow start"),
            idempotency_key=payload["idempotency_key"],
            duplicate_protection=True,
        )
        session.add(record)
        await append_event(
            session, UUID(payload["run_id"]), payload["tenant_id"], "WORKER_FAILED",
            {"failure_id": payload["failure_id"], "reason": payload["reason"], "worker_id": record.worker_id},
        )
        await session.commit()


@activity.defn
async def mark_worker_failure_recovered_activity(payload: dict[str, Any]) -> None:
    async with session_for_tenant(payload.get("tenant_id")) as session:
        record = await session.get(WorkerFailureRecord, UUID(payload["failure_id"]))
        if record is None:
            return
        record.recovery_status = "RECOVERED"
        record.recovery_count = int(payload.get("recovery_count", record.recovery_count + 1))
        record.recovery_attempt = int(payload.get("recovery_attempt", record.recovery_attempt))
        record.recovery_completed_at = datetime.now(timezone.utc)
        await append_event(
            session, record.run_id, record.tenant_id, "RESUMED",
            {"failure_id": payload["failure_id"], "checkpoint_id": payload.get("checkpoint_id")},
        )
        await session.commit()


@activity.defn
async def record_worker_recovery_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """记录 Worker 崩溃后的 Temporal 驱动恢复。幂等，按 (failure_id) 去重。

    恢复时重新验证用户仍是 active tenant member（必要权限校验）。
    """
    from agent_runtime.db import TenantMembership, User

    tenant_id = payload["tenant_id"]
    run_id = UUID(payload["run_id"])
    user_id = payload.get("user_id", "")
    attempt = int(payload.get("workflow_attempt", 1))
    async with session_for_tenant(tenant_id) as session:
        user = (await session.execute(
            select(User).where(User.user_id == user_id, User.status == "active")
        )).scalar_one_or_none()
        membership = (await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
            )
        )).scalar_one_or_none()
        authorized = user is not None and membership is not None
        if not authorized:
            await append_event(session, run_id, tenant_id, "RECOVERY_AUTH_FAILED", {
                "user_id": user_id, "workflow_attempt": attempt,
            })
            await session.commit()
            return {"authorized": False}

        existing = (await session.execute(
            select(WorkerFailureRecord).where(WorkerFailureRecord.idempotency_key == payload["idempotency_key"])
        )).scalar_one_or_none()
        if existing is None:
            failure_id = UUID(payload["failure_id"])
            now = datetime.now(timezone.utc)
            existing = WorkerFailureRecord(
                failure_id=failure_id,
                run_id=run_id,
                tenant_id=tenant_id,
                worker_id=payload.get("worker_id", "temporal-worker"),
                reason=f"worker process lost before workflow attempt {attempt}",
                recovery_status="RECOVERING",
                recovery_count=attempt,
                retry_count=0,
                recovery_position=payload.get("recovery_position", "temporal-history-replay"),
                idempotency_key=payload["idempotency_key"],
                duplicate_protection=True,
            )
            session.add(existing)
        existing.recovery_started_at = existing.recovery_started_at or datetime.now(timezone.utc)
        existing.recovery_attempt = attempt
        existing.fencing_token = attempt
        await append_event(session, run_id, tenant_id, "WORKER_FAILED", {
            "workflow_attempt": attempt, "reason": "worker process lost (Temporal auto-redispatch)",
        })
        await append_event(session, run_id, tenant_id, "TEMPORAL_HISTORY_REPLAY", {"workflow_attempt": attempt})
        await append_event(session, run_id, tenant_id, "WORKER_RECONNECTED", {"workflow_attempt": attempt})
        await append_event(session, run_id, tenant_id, "WORKFLOW_RESUMED", {"workflow_attempt": attempt})
        await session.commit()
        return {"authorized": True, "failure_id": str(existing.failure_id)}


@activity.defn
async def record_checkpoint_restore_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """B7: record a checkpoint restore in the audit trail, idempotently.

    Re-validates that the requester is an active member of the target tenant at
    restore time (ACL/capability consistency), and records the immutable source
    checkpoint identity so replay/reconciliation can attribute the new run.
    """
    from agent_runtime.db import TenantMembership, User

    tenant_id = payload["tenant_id"]
    run_id = UUID(payload["run_id"])
    user_id = payload.get("user_id", "")
    idempotency_key = payload["idempotency_key"]
    async with session_for_tenant(tenant_id) as session:
        user = (await session.execute(
            select(User).where(User.user_id == user_id, User.status == "active")
        )).scalar_one_or_none()
        membership = (await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
            )
        )).scalar_one_or_none()
        if user is None or membership is None:
            await append_event(session, run_id, tenant_id, "RESTORE_AUTH_FAILED", {"user_id": user_id})
            await session.commit()
            return {"authorized": False}

        marker = {"checkpoint_id": payload.get("checkpoint_id"), "recovery_source": "checkpoint_restore"}
        existing = (await session.execute(
            select(RunEvent).where(
                RunEvent.run_id == run_id,
                RunEvent.tenant_id == tenant_id,
                RunEvent.event_type == "CHECKPOINT_RESTORE_STARTED",
                RunEvent.payload.contains(marker),
            )
        )).scalar_one_or_none()
        if existing is None:
            await append_event(session, run_id, tenant_id, "CHECKPOINT_RESTORE_STARTED", {
                **marker,
                "source_run_id": payload.get("source_run_id"),
                "checkpoint_sequence": payload.get("checkpoint_sequence"),
                "state_digest": payload.get("state_digest"),
                "restored_by": user_id,
                "idempotency_key": idempotency_key,
            })
            await session.commit()
        return {"authorized": True, "recorded": existing is None}


@activity.defn
async def append_history_event_activity(payload: dict[str, Any]) -> None:
    async with session_for_tenant(payload.get("tenant_id")) as session:
        await append_event(
            session,
            UUID(payload["run_id"]),
            payload["tenant_id"],
            payload["event_type"],
            payload.get("payload", {}),
        )
        await session.commit()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@activity.defn
async def create_approval_binding_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """在审批请求时创建强绑定 Approval Action Envelope。"""
    from agent_runtime.approval.envelope import ApprovalEnvelope
    from agent_runtime.approval.service import ApprovalService
    from agent_runtime.db import ApprovalBinding
    from uuid import uuid4

    approval_id = str(uuid4())
    nonce = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    envelope = ApprovalEnvelope(
        approval_id=approval_id,
        tenant_id=payload["tenant_id"],
        run_id=payload["run_id"],
        tool_call_id=payload["tool_call_id"],
        tool_name=payload["tool_name"],
        target_resource_id=payload.get("target_resource_id", ""),
        operation_type=payload.get("operation_type", ""),
        canonical_arguments=payload.get("arguments", {}),
        requested_by=payload["requested_by"],
        policy_version=payload.get("policy_version", "v1"),
        risk_level=payload.get("risk_level", "HIGH"),
        nonce=nonce,
        expires_at=expires_at,
    )
    async with session_for_tenant(payload["tenant_id"]) as session:
        record = await ApprovalService().create(session, tenant_id=payload["tenant_id"], envelope=envelope)
        await session.commit()
        return {"approval_id": str(record.approval_id), "action_hash": envelope.action_hash(),
                "arguments_hash": envelope.arguments_hash(), "expires_at": expires_at}


@activity.defn
async def validate_approval_binding_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """工具执行前重验审批绑定并返回 approval_id/version 供消费。"""
    from agent_runtime.approval.service import ApprovalService
    from agent_runtime.db import ApprovalBinding

    async with session_for_tenant(payload["tenant_id"]) as session:
        record = await ApprovalService().validate_binding(
            session, tenant_id=payload["tenant_id"],
            run_id=payload["run_id"], tool_call_id=payload["tool_call_id"],
            tool_name=payload["tool_name"], target_resource_id=payload.get("target_resource_id", ""),
            arguments=payload.get("arguments", {}), policy_version=payload.get("policy_version", "v1"),
            approver_id=payload.get("approver_id"),
        )
        return {"approval_id": str(record.approval_id), "version": record.version,
                "approver_id": record.approver_id}


@activity.defn
async def consume_approval_binding_activity(payload: dict[str, Any]) -> None:
    """工具执行成功后原子消费审批，防止重复授权。"""
    from agent_runtime.approval.service import ApprovalService

    async with session_for_tenant(payload["tenant_id"]) as session:
        await ApprovalService().consume(
            session, tenant_id=payload["tenant_id"],
            approval_id=payload["approval_id"], version=int(payload["version"]),
        )
        await session.commit()
