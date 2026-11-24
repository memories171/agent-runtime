from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.db import ToolInvocation, append_event

TERMINAL_STATUSES = {"CONFIRMED", "FAILED", "RECONCILIATION_REQUIRED"}
ACTIVE_STATUSES = {"CREATED", "DISPATCHED", "UNKNOWN"}

# Temporal Activity attempts occupy a small numeric space (1, 2, 3 ...). Human
# reconciliation writes into a disjoint high space. Once that happens, the next
# Activity attempt is shifted into the same high namespace so an explicit retry
# can proceed while stale low-number attempts remain fenced out.
HUMAN_RECON_FENCING_BASE = 1_000_000


def human_reconciliation_fencing_token(record: ToolInvocation) -> int:
    """Fencing token for a human decision, in a namespace above all Activity attempts."""
    seq = int(getattr(record, "attempt_count", 0) or 0) + 1
    return HUMAN_RECON_FENCING_BASE + seq


def activity_fencing_token(record: ToolInvocation | None, attempt: int) -> int:
    """Activity token; switch namespace once a human decision has fenced the journal."""
    attempt = int(attempt)
    if record is not None and int(getattr(record, "fencing_token", 0) or 0) >= HUMAN_RECON_FENCING_BASE:
        return HUMAN_RECON_FENCING_BASE + attempt
    return attempt


VALID_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"DISPATCHED", "FAILED"},
    "DISPATCHED": {"CONFIRMED", "FAILED", "UNKNOWN"},
    "FAILED": {"DISPATCHED"},
    "UNKNOWN": {"RECONCILIATION_REQUIRED", "CONFIRMED", "FAILED"},
    "CONFIRMED": set(),
    "RECONCILIATION_REQUIRED": {"CONFIRMED", "FAILED"},
}


class InvocationConflictError(RuntimeError):
    pass


class InvocationUnknownError(RuntimeError):
    pass


def canonical_arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_tool_idempotency_key(tenant_id: str, run_id: str, tool_call_id: str) -> str:
    """Stable key: no retry_count, no attempt_id, no arguments.

    Same logical tool call across Temporal Activity retries always maps to one key.
    """
    raw = f"{tenant_id}:{run_id}:{tool_call_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ToolInvocationService:
    """基于 ToolInvocation Journal 的幂等、状态机、Lease/Fencing 与对账服务。

    所有写操作使用条件更新（基于 status / fencing_token / lease），
    不依赖 Python 侧读后写覆盖，避免多个 Worker 并发覆盖状态。
    """

    def __init__(self, lease_seconds: int = 120) -> None:
        self._lease_seconds = lease_seconds

    @staticmethod
    async def _get(
        session: AsyncSession, tenant_id: str, idempotency_key: str
    ) -> ToolInvocation | None:
        result = await session.execute(
            select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant_id,
                ToolInvocation.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def fencing_token_for_attempt(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        attempt: int,
    ) -> int:
        record = await self._get(session, tenant_id, idempotency_key)
        return activity_fencing_token(record, attempt)

    async def begin(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        tenant_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        fencing_token: int,
        lease_owner: str,
        idempotent_provider: bool = False,
        event_appender=append_event,
    ) -> dict[str, Any]:
        """创建或获取 invocation，返回决策：reuse / execute / conflict / unknown。

        - 原子插入：唯一索引 (tenant_id, idempotency_key) 保证同租户幂等键唯一。
        - 参数 hash 不一致：拒绝执行并记录安全事件。
        - CONFIRMED：复用已有结果，不重复外部副作用。
        - DISPATCHED/UNKNOWN 且 Lease 未过期：由本 Worker 继续持有，转 UNKNOWN 隔离。
        - FAILED：允许重新 DISPATCHED（明确失败可安全重试）。
        """
        arg_hash = canonical_arguments_hash(arguments)
        now = datetime.now(timezone.utc)
        record = await self._get(session, tenant_id, idempotency_key)

        if record is None:
            record = ToolInvocation(
                invocation_id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                idempotency_key=idempotency_key,
                canonical_arguments_hash=arg_hash,
                status="CREATED",
                attempt_count=1,
                started_at=now,
                fencing_token=fencing_token,
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            )
            session.add(record)
            try:
                await session.flush()
            except Exception:
                await session.rollback()
                # rollback 会结束事务并清掉 SET LOCAL 的租户上下文，必须重设，
                # 否则下面的补偿重读会被 RLS 全部过滤成空。
                from agent_runtime.rls import set_tenant_context
                await set_tenant_context(session, tenant_id)
                record = await self._get(session, tenant_id, idempotency_key)
                if record is None:
                    raise
            else:
                await event_appender(session, run_id, tenant_id, "TOOL_INTENT_CREATED", {
                    "tool_call_id": tool_call_id,
                    "idempotency_key": idempotency_key,
                    "canonical_arguments_hash": arg_hash,
                    "fencing_token": fencing_token,
                })
                await self._transition(
                    session, record, "DISPATCHED", fencing_token, lease_owner
                )
                await session.commit()
                return {"decision": "execute", "invocation_id": str(record.invocation_id), "arg_hash": arg_hash}

        if record.canonical_arguments_hash and record.canonical_arguments_hash != arg_hash:
            await event_appender(session, run_id, tenant_id, "TOOL_ARGUMENT_CONFLICT", {
                "idempotency_key": idempotency_key,
                "expected_hash": record.canonical_arguments_hash,
                "actual_hash": arg_hash,
            })
            await session.commit()
            raise InvocationConflictError(
                f"idempotency key {idempotency_key} reused with different arguments"
            )

        if record.status == "CONFIRMED" and record.result is not None:
            await event_appender(session, run_id, tenant_id, "TOOL_EXECUTION_CONFIRMED", {
                "idempotency_key": idempotency_key, "reused": True,
            })
            await session.commit()
            return {
                "decision": "reuse", "result": record.result,
                "invocation_id": str(record.invocation_id), "arg_hash": arg_hash,
            }

        if record.status in {"UNKNOWN", "RECONCILIATION_REQUIRED"}:
            await session.commit()
            raise InvocationUnknownError(
                f"tool outcome is {record.status}; reconcile with provider before retry"
            )

        if record.status == "DISPATCHED":
            if idempotent_provider:
                # Provider is idempotent on the same key: safe to re-dispatch after a crash.
                outcome = await session.execute(
                    update(ToolInvocation)
                    .where(
                        ToolInvocation.invocation_id == record.invocation_id,
                        ToolInvocation.status == "DISPATCHED",
                        ToolInvocation.fencing_token <= fencing_token,
                    )
                    .values(
                        fencing_token=fencing_token,
                        lease_owner=lease_owner,
                        lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                        attempt_count=record.attempt_count + 1,
                    )
                )
                if outcome.rowcount != 1:
                    await session.commit()
                    raise InvocationConflictError("fencing token check failed on idempotent redispatch")
                await event_appender(session, run_id, tenant_id, "TOOL_IDEMPOTENT_REDISPATCH", {
                    "idempotency_key": idempotency_key, "attempt_count": record.attempt_count + 1,
                })
                await session.commit()
                return {"decision": "execute", "invocation_id": str(record.invocation_id), "arg_hash": arg_hash}
            # Non-idempotent provider: previous dispatch may have executed an external side effect.
            # Fail closed into UNKNOWN regardless of lease/fencing; only reconciliation can resolve it.
            await self._mark_unknown(session, record, fencing_token, event_appender)
            await session.commit()
            raise InvocationUnknownError(
                "previous dispatch outcome is unknown; reconcile before retry"
            )

        if record.status == "FAILED":
            await self._transition(session, record, "DISPATCHED", fencing_token, lease_owner)
            record.attempt_count += 1
            record.started_at = now
            record.last_error = None
            await event_appender(session, run_id, tenant_id, "TOOL_RETRY_DISPATCHED", {
                "idempotency_key": idempotency_key, "attempt_count": record.attempt_count,
            })
            await session.commit()
            return {"decision": "execute", "invocation_id": str(record.invocation_id), "arg_hash": arg_hash}

        if record.status == "CREATED":
            await self._transition(session, record, "DISPATCHED", fencing_token, lease_owner)
            await session.commit()
            return {"decision": "execute", "invocation_id": str(record.invocation_id), "arg_hash": arg_hash}

        await session.commit()
        raise InvocationConflictError(f"invocation in unsupported state: {record.status}")

    async def _transition(
        self,
        session: AsyncSession,
        record: ToolInvocation,
        target: str,
        fencing_token: int,
        lease_owner: str,
    ) -> None:
        allowed = VALID_TRANSITIONS.get(record.status, set())
        if target not in allowed:
            raise InvocationConflictError(
                f"illegal transition {record.status} -> {target}"
            )
        now = datetime.now(timezone.utc)
        result = await session.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.invocation_id == record.invocation_id,
                ToolInvocation.status == record.status,
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status=target,
                fencing_token=fencing_token,
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            )
        )
        if result.rowcount != 1:
            raise InvocationConflictError(
                "fencing token or status check failed; stale worker write rejected"
            )
        record.status = target
        record.fencing_token = fencing_token

    async def _mark_unknown(
        self, session: AsyncSession, record: ToolInvocation, fencing_token: int, event_appender
    ) -> None:
        result = await session.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.invocation_id == record.invocation_id,
                # Guard against overwriting a concurrently confirmed/failed terminal
                # state: only the status we read is eligible for the UNKNOWN marker.
                ToolInvocation.status == record.status,
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status="UNKNOWN",
                fencing_token=fencing_token,
                lease_expires_at=datetime.now(timezone.utc),
                last_error="outcome unknown after worker/activity interruption",
            )
        )
        if result.rowcount == 1:
            record.status = "UNKNOWN"
            await event_appender(session, record.run_id, record.tenant_id, "TOOL_OUTCOME_UNKNOWN", {
                "idempotency_key": record.idempotency_key,
            })

    async def confirm(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        fencing_token: int,
        result: dict[str, Any],
        external_reference: str | None = None,
        result_reference: str | None = None,
        event_appender=append_event,
    ) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(ToolInvocation)
            .where(
                ToolInvocation.tenant_id == tenant_id,
                ToolInvocation.idempotency_key == idempotency_key,
                ToolInvocation.status == "DISPATCHED",
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status="CONFIRMED",
                result=result,
                external_reference=external_reference,
                result_reference=result_reference,
                completed_at=now,
                fencing_token=fencing_token,
                lease_expires_at=None,
                last_error=None,
            )
        )
        outcome = await session.execute(stmt)
        if outcome.rowcount != 1:
            raise InvocationConflictError("confirm rejected: stale fencing token or status mismatch")
        record = await self._get(session, tenant_id, idempotency_key)
        await event_appender(session, record.run_id, tenant_id, "TOOL_EXECUTION_CONFIRMED", {
            "idempotency_key": idempotency_key, "fencing_token": fencing_token,
        })

    async def mark_unknown(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        fencing_token: int,
        last_error: str,
        event_appender=append_event,
    ) -> None:
        record = await self._get(session, tenant_id, idempotency_key)
        if record is None or record.status in TERMINAL_STATUSES:
            return
        await self._mark_unknown(session, record, fencing_token, event_appender)
        record.last_error = last_error[:1000]

    async def mark_failed(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        fencing_token: int,
        last_error: str,
        event_appender=append_event,
    ) -> None:
        record = await self._get(session, tenant_id, idempotency_key)
        if record is None or record.status not in {"DISPATCHED", "CREATED"}:
            return
        outcome = await session.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.invocation_id == record.invocation_id,
                ToolInvocation.status == record.status,
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status="FAILED", fencing_token=fencing_token,
                lease_expires_at=None, last_error=last_error[:1000],
            )
        )
        if outcome.rowcount == 1:
            await event_appender(session, record.run_id, tenant_id, "TOOL_FAILED", {
                "idempotency_key": idempotency_key, "error": last_error[:300],
            })

    async def require_reconciliation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        fencing_token: int,
        reason: str,
        event_appender=append_event,
    ) -> None:
        record = await self._get(session, tenant_id, idempotency_key)
        if record is None:
            return
        # Idempotent: an invocation already awaiting human reconciliation needs no
        # further writes or duplicate audit events (Activity retries may call again).
        if record.status == "RECONCILIATION_REQUIRED":
            return
        # Already finalised concurrently (e.g. duplicate delivery confirm raced the
        # unknown marker): nothing to reconcile. Do not mask the caller's exception.
        if record.status in {"CONFIRMED", "FAILED"}:
            return
        if record.status != "UNKNOWN":
            raise InvocationConflictError("reconciliation requires UNKNOWN status")
        outcome = await session.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.invocation_id == record.invocation_id,
                ToolInvocation.status == "UNKNOWN",
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status="RECONCILIATION_REQUIRED",
                reconciliation_status="PENDING",
                reconciliation_note=reason[:1000],
                fencing_token=fencing_token,
            )
        )
        if outcome.rowcount != 1:
            # A concurrent writer already moved it to RECONCILIATION_REQUIRED.
            # Treat as success (idempotent) rather than a spurious 409.
            current = await self._get(session, tenant_id, idempotency_key)
            if current is not None and current.status == "RECONCILIATION_REQUIRED":
                return
            raise InvocationConflictError("fencing token check failed on reconciliation")
        await event_appender(session, record.run_id, tenant_id, "TOOL_RECONCILIATION_REQUIRED", {
            "idempotency_key": idempotency_key, "reason": reason[:500],
        })

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        fencing_token: int,
        resolved_status: str,
        resolved_result: dict[str, Any] | None,
        actor: str,
        note: str,
        event_appender=append_event,
    ) -> dict[str, Any]:
        if resolved_status not in {"CONFIRMED", "FAILED"}:
            raise InvocationConflictError("reconciliation result must be CONFIRMED or FAILED")
        record = await self._get(session, tenant_id, idempotency_key)
        if record is None:
            raise InvocationConflictError("invocation not found")
        # Idempotent replay: the same human decision already finalised this
        # invocation. Return success without a second terminal write/event.
        if (
            record.status == resolved_status
            and record.reconciliation_status == "RESOLVED"
        ):
            return {
                "idempotency_key": idempotency_key,
                "status": resolved_status,
                "reused": True,
            }
        if record.status not in {"UNKNOWN", "RECONCILIATION_REQUIRED"}:
            raise InvocationConflictError("only UNKNOWN / RECONCILIATION_REQUIRED can be reconciled")
        outcome = await session.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.invocation_id == record.invocation_id,
                ToolInvocation.status == record.status,
                ToolInvocation.fencing_token <= fencing_token,
            )
            .values(
                status=resolved_status,
                result=resolved_result,
                reconciliation_status="RESOLVED",
                reconciliation_note=f"{actor}: {note}"[:1000],
                completed_at=datetime.now(timezone.utc),
                fencing_token=fencing_token,
                lease_expires_at=None,
            )
        )
        if outcome.rowcount != 1:
            raise InvocationConflictError("reconcile rejected: stale fencing token or status mismatch")
        await event_appender(session, record.run_id, tenant_id, "TOOL_RECONCILED", {
            "idempotency_key": idempotency_key, "resolved_status": resolved_status, "actor": actor,
        })
        return {"idempotency_key": idempotency_key, "status": resolved_status}
