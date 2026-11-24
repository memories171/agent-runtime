from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.authorization.policies import AuthorizationDecision, AuthorizationRequest, evaluate as evaluate_request


def evaluate(request: AuthorizationRequest) -> AuthorizationDecision:
    return evaluate_request(request)


async def _has_acl(session: AsyncSession, request: AuthorizationRequest) -> bool:
    from agent_runtime.db import FinancialResourceGrant, ResourceAcl

    stmt = select(ResourceAcl.acl_id).where(
        ResourceAcl.tenant_id == request.effective_tenant_id,
        ResourceAcl.user_id == request.principal.user_id,
        ResourceAcl.resource_type.in_([request.resource_type, "*"]),
        ResourceAcl.resource_id.in_([request.resource_id, "*"]),
        ResourceAcl.permission.in_([request.permission or request.action, "*"]),
    ).limit(1)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return True

    if request.resource_type.startswith("financial"):
        grant_stmt = select(FinancialResourceGrant.grant_id).where(
            FinancialResourceGrant.tenant_id == request.effective_tenant_id,
            FinancialResourceGrant.user_id == request.principal.user_id,
            FinancialResourceGrant.resource_type.in_([request.resource_type, "*"]),
            FinancialResourceGrant.resource_id.in_([request.resource_id, "*"]),
            FinancialResourceGrant.permission.in_([request.permission or request.action, "*"]),
        ).limit(1)
        if (await session.execute(grant_stmt)).scalar_one_or_none() is not None:
            return True
    return False


async def authorize(session: AsyncSession, request: AuthorizationRequest) -> AuthorizationDecision:
    from agent_runtime.authorization.audit import record_decision

    decision = evaluate(request)
    if decision.allowed and request.require_acl and not await _has_acl(session, request):
        decision = AuthorizationDecision.deny("resource ACL grant is missing")
    await record_decision(session, request, decision)
    return decision
