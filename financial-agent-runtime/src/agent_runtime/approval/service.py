from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import update

from agent_runtime.approval.envelope import ApprovalEnvelope, canonical_arguments_hash, canonical_json
from agent_runtime.db import ApprovalBinding, append_event

TERMINAL = {"APPROVED", "CONSUMED", "REJECTED", "EXPIRED", "REVOKED", "INVALIDATED"}


class ApprovalError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class InvalidApprovalError(ApprovalError):
    def __init__(self, detail: str):
        super().__init__(detail, status_code=409)


class ApprovalStateChangedError(InvalidApprovalError):
    """A terminal state change was staged; callers must commit before returning.

    Raised after EXPIRED/INVALIDATED and its audit event are written to the
    session. Without an explicit commit, session teardown rolls the change back.
    """


class ApprovalService:
    """复用现有 RunEvent 审计；为审批决策增加强绑定状态机。"""

    async def create(self, session: Any, *, tenant_id: str, envelope: ApprovalEnvelope) -> ApprovalBinding:
        existing = (await session.execute(
            __import__("sqlalchemy").select(ApprovalBinding).where(
                ApprovalBinding.tenant_id == tenant_id,
                ApprovalBinding.run_id == __import__("uuid").UUID(envelope.run_id),
                ApprovalBinding.tool_call_id == envelope.tool_call_id,
                ApprovalBinding.action_hash == envelope.action_hash(),
            )
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        record = ApprovalBinding(
            approval_id=__import__("uuid").UUID(envelope.approval_id),
            tenant_id=tenant_id,
            run_id=__import__("uuid").UUID(envelope.run_id),
            tool_call_id=envelope.tool_call_id,
            tool_name=envelope.tool_name,
            target_resource_id=envelope.target_resource_id,
            operation_type=envelope.operation_type,
            canonical_arguments=canonical_json(envelope.canonical_arguments),
            canonical_arguments_hash=envelope.arguments_hash(),
            action_hash=envelope.action_hash(),
            requested_by=envelope.requested_by,
            policy_version=envelope.policy_version,
            risk_level=envelope.risk_level,
            nonce=envelope.nonce,
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.fromisoformat(envelope.expires_at.replace("Z", "+00:00")),
            approval_status="PENDING",
        )
        session.add(record)
        await append_event(session, record.run_id, tenant_id, "APPROVAL_REQUIRED", {
            "approval_id": envelope.approval_id,
            "tool": envelope.tool_name,
            "tool_call_id": envelope.tool_call_id,
            "action_hash": envelope.action_hash(),
            "arguments_hash": envelope.arguments_hash(),
            "target_resource_id": envelope.target_resource_id,
            "policy_version": envelope.policy_version,
            "risk_level": envelope.risk_level,
        })
        return record

    async def decide(
        self,
        session: Any,
        *,
        tenant_id: str,
        approval_id: str,
        approved: bool,
        approver_id: str,
        reason: str | None = None,
    ) -> ApprovalBinding:
        from sqlalchemy import select
        record = (await session.execute(
            select(ApprovalBinding).where(
                ApprovalBinding.approval_id == __import__("uuid").UUID(approval_id),
                ApprovalBinding.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if record is None:
            raise ApprovalError("approval not found", 404)
        if record.approval_status in TERMINAL:
            # 幂等：重复提交同一决策直接返回，不重复消费。
            if record.approval_status == ("APPROVED" if approved else "REJECTED") and record.approver_id == approver_id:
                return record
            raise InvalidApprovalError(f"approval already in terminal state: {record.approval_status}")
        if record.expires_at <= datetime.now(timezone.utc):
            outcome = await session.execute(
                update(ApprovalBinding)
                .where(ApprovalBinding.approval_id == record.approval_id, ApprovalBinding.approval_status == "PENDING")
                .values(approval_status="EXPIRED", version=ApprovalBinding.version + 1)
            )
            if outcome.rowcount == 1:
                await append_event(session, record.run_id, tenant_id, "APPROVAL_EXPIRED", {"approval_id": approval_id})
            raise ApprovalStateChangedError("approval expired")

        target_status = "APPROVED" if approved else "REJECTED"
        outcome = await session.execute(
            update(ApprovalBinding)
            .where(
                ApprovalBinding.approval_id == record.approval_id,
                ApprovalBinding.approval_status == "PENDING",
                ApprovalBinding.version == record.version,
            )
            .values(
                approval_status=target_status, approver_id=approver_id, reason=reason,
                version=ApprovalBinding.version + 1,
            )
        )
        if outcome.rowcount != 1:
            raise InvalidApprovalError("concurrent approval decision conflict")
        await append_event(session, record.run_id, tenant_id,
                           "APPROVAL_DECISION" if approved else "APPROVAL_REJECTION", {
            "approval_id": approval_id, "approver_user_id": approver_id,
            "approved": approved, "reason": reason,
        })
        await session.refresh(record)
        return record

    async def validate_binding(
        self,
        session: Any,
        *,
        tenant_id: str,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        target_resource_id: str,
        arguments: dict[str, Any],
        policy_version: str,
        approver_id: str | None = None,
    ) -> ApprovalBinding:
        """工具执行前重验：任何绑定字段不一致/过期/已消费都拒绝。"""
        from sqlalchemy import select
        args_hash = canonical_arguments_hash(arguments)
        records = (await session.execute(
            select(ApprovalBinding).where(
                ApprovalBinding.tenant_id == tenant_id,
                ApprovalBinding.run_id == __import__("uuid").UUID(run_id),
                ApprovalBinding.tool_call_id == tool_call_id,
                ApprovalBinding.approval_status == "APPROVED",
            )
        )).scalars()
        candidates = list(records)
        if not candidates:
            raise InvalidApprovalError("no approved binding for this tool call")
        state_changed = False
        for record in candidates:
            if record.tool_name != tool_name or record.target_resource_id != target_resource_id:
                continue
            if record.canonical_arguments_hash != args_hash:
                state_changed |= await self._invalidate(session, record, "arguments changed")
                continue
            if record.policy_version != policy_version:
                state_changed |= await self._invalidate(session, record, "policy version changed")
                continue
            if record.expires_at <= datetime.now(timezone.utc):
                state_changed |= await self._invalidate(session, record, "approval expired")
                continue
            if approver_id and record.approver_id != approver_id:
                continue
            return record
        if state_changed:
            raise ApprovalStateChangedError("approval binding invalid for this operation")
        raise InvalidApprovalError("approval binding invalid for this operation")

    async def consume(self, session: Any, *, tenant_id: str, approval_id: str, version: int) -> None:
        """将 APPROVED 原子转为 CONSUMED，防止同一审批重复授权执行。"""
        from sqlalchemy import select
        record = (await session.execute(
            select(ApprovalBinding).where(
                ApprovalBinding.approval_id == __import__("uuid").UUID(approval_id),
                ApprovalBinding.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if record is None:
            raise InvalidApprovalError("approval not found")
        outcome = await session.execute(
            update(ApprovalBinding)
            .where(
                ApprovalBinding.approval_id == record.approval_id,
                ApprovalBinding.tenant_id == tenant_id,
                ApprovalBinding.approval_status == "APPROVED",
                ApprovalBinding.version == version,
            )
            .values(
                approval_status="CONSUMED", consumed_at=datetime.now(timezone.utc),
                version=ApprovalBinding.version + 1,
            )
        )
        if outcome.rowcount != 1:
            raise InvalidApprovalError("approval already consumed or version conflict")
        await append_event(session, record.run_id, tenant_id, "APPROVAL_CONSUMED", {"approval_id": approval_id})

    async def _invalidate(self, session: Any, record: ApprovalBinding, reason: str) -> bool:
        if record.approval_status in {"CONSUMED", "INVALIDATED"}:
            return False
        outcome = await session.execute(
            update(ApprovalBinding)
            .where(
                ApprovalBinding.approval_id == record.approval_id,
                ApprovalBinding.approval_status == "APPROVED",
                ApprovalBinding.version == record.version,
            )
            .values(approval_status="INVALIDATED", version=ApprovalBinding.version + 1)
        )
        if outcome.rowcount == 1:
            await append_event(session, record.run_id, record.tenant_id, "APPROVAL_INVALIDATED", {"reason": reason})
        return outcome.rowcount == 1

    async def revoke(self, session: Any, *, tenant_id: str, approval_id: str) -> None:
        from sqlalchemy import select
        record = (await session.execute(
            select(ApprovalBinding).where(
                ApprovalBinding.approval_id == __import__("uuid").UUID(approval_id),
                ApprovalBinding.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if record is None:
            raise ApprovalError("approval not found", 404)
        if record.approval_status == "CONSUMED":
            raise InvalidApprovalError("consumed approval cannot be revoked")
        if record.approval_status in {"REVOKED", "REJECTED", "INVALIDATED", "EXPIRED"}:
            return
        outcome = await session.execute(
            update(ApprovalBinding)
            .where(ApprovalBinding.approval_id == record.approval_id, ApprovalBinding.version == record.version)
            .values(approval_status="REVOKED", version=ApprovalBinding.version + 1)
        )
        if outcome.rowcount == 1:
            await append_event(session, record.run_id, tenant_id, "APPROVAL_REVOKED", {"approval_id": approval_id})
