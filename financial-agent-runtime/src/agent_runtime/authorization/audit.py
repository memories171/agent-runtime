from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.authorization.policies import AuthorizationDecision, AuthorizationRequest
from agent_runtime.db import AuthAuditLog


async def record_decision(
    session: AsyncSession,
    request: AuthorizationRequest,
    decision: AuthorizationDecision,
    action_label: str = "AUTHZ_DECISION",
) -> AuthAuditLog:
    record = AuthAuditLog(
        tenant_id=request.effective_tenant_id,
        actor_user_id=request.principal.user_id,
        action=action_label,
        outcome="ALLOW" if decision.allowed else "DENY",
        reason=decision.reason,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        details={
            "action": request.action,
            "permission": request.permission,
            "roles": sorted(request.principal.roles),
            "authentication_method": request.principal.authentication_method,
            "context": request.context,
        },
    )
    session.add(record)
    await session.flush()
    return record


async def record_auth_event(
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
    action: str,
    outcome: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuthAuditLog:
    record = AuthAuditLog(
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action=action,
        outcome=outcome,
        reason=reason,
        resource_type="identity",
        resource_id=user_id,
        details=details or {},
    )
    session.add(record)
    await session.flush()
    return record
