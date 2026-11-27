from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.auth import get_principal, require_permission
from agent_runtime.contracts import Principal
from agent_runtime.db import (
    AuthAuditLog,
    Permission,
    Role,
    RolePermission,
    Tenant,
    TenantMembership,
    User,
    UserRole,
    get_session,
)
from agent_runtime.rls import set_current_tenant, system_session

router = APIRouter(prefix="/api/v1")


class TenantSwitchRequest(BaseModel):
    tenant_id: str


def _principal_payload(principal: Principal) -> dict:
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "roles": sorted(principal.roles),
        "permissions": sorted(principal.permissions),
        "scopes": sorted(principal.scopes),
        "authentication_method": principal.authentication_method,
        "token_expires_at": principal.token_expires_at.isoformat() if principal.token_expires_at else None,
    }


@router.get("/auth/me")
async def auth_me(principal: Principal = Depends(get_principal)):
    return _principal_payload(principal)


@router.get("/auth/tenants")
async def auth_tenants(
    principal: Principal = Depends(get_principal),
):
    from agent_runtime.db import SessionLocal

    # Membership rows are tenant-scoped by RLS; the current tenant session
    # cannot see memberships in other tenants, so resolve via system context.
    async with system_session(SessionLocal, "identity_resolver") as session:
        result = await session.execute(
            select(Tenant.tenant_id, Tenant.display_name, TenantMembership.status, TenantMembership.organization_id)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.tenant_id)
            .where(
                TenantMembership.user_id == principal.user_id,
                TenantMembership.status == "active",
                or_(
                    TenantMembership.expires_at.is_(None),
                    TenantMembership.expires_at >= datetime.now(timezone.utc),
                ),
            )
        )
        rows = result.all()
    return [
        {
            "tenant_id": row[0],
            "display_name": row[1],
            "status": row[2],
            "organization_id": row[3],
        }
        for row in rows
    ]


@router.post("/auth/switch-tenant")
async def switch_tenant(
    body: TenantSwitchRequest,
    principal: Principal = Depends(get_principal),
):
    from agent_runtime.auth import _load_identity
    from agent_runtime.db import SessionLocal

    async with system_session(SessionLocal, "identity_resolver") as session:
        result = await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == principal.user_id,
                TenantMembership.tenant_id == body.tenant_id,
                TenantMembership.status == "active",
                or_(
                    TenantMembership.expires_at.is_(None),
                    TenantMembership.expires_at >= datetime.now(timezone.utc),
                ),
            )
        )
        membership = result.scalar_one_or_none()
    if membership is None:
        async with system_session(SessionLocal, "identity_resolver") as session:
            session.add(AuthAuditLog(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                action="TENANT_SWITCH",
                result="DENY",
                reason="target tenant active membership not found or expired",
                details={"from_tenant": principal.tenant_id, "to_tenant": body.tenant_id},
            ))
            await session.commit()
        raise HTTPException(status_code=403, detail="user is not an active member of requested tenant")

    # Re-resolve identity from the DB for the target tenant. Never carry over
    # roles/permissions/scopes from the previously selected tenant.
    target = await _load_identity(principal.user_id, body.tenant_id)
    if target is None:
        raise HTTPException(status_code=403, detail="identity cannot be resolved for requested tenant")
    target.token_expires_at = principal.token_expires_at
    target.authentication_method = principal.authentication_method
    set_current_tenant(body.tenant_id)

    async with system_session(SessionLocal, "identity_resolver") as session:
        session.add(AuthAuditLog(
            user_id=principal.user_id,
            tenant_id=body.tenant_id,
            action="TENANT_SWITCH",
            result="ALLOW",
            details={
                "from_tenant": principal.tenant_id,
                "to_tenant": body.tenant_id,
                "roles": sorted(target.roles),
                "permissions": sorted(target.permissions),
            },
        ))
        await session.commit()
    return _principal_payload(target)


@router.get("/auth/permissions")
async def auth_permissions(principal: Principal = Depends(get_principal)):
    return {"permissions": sorted(principal.permissions), "scopes": sorted(principal.scopes)}


@router.get("/admin/users")
async def admin_users(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "user:manage")
    result = await session.execute(
        select(User.user_id, User.email, User.display_name, User.status, TenantMembership.tenant_id)
        .join(TenantMembership, TenantMembership.user_id == User.user_id)
        .where(TenantMembership.tenant_id == principal.tenant_id)
        .order_by(User.user_id)
    )
    return [
        {"user_id": row[0], "email": row[1], "display_name": row[2], "status": row[3], "tenant_id": row[4]}
        for row in result.all()
    ]


@router.get("/admin/roles")
async def admin_roles(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "user:manage")
    result = await session.execute(
        select(Role.role_id, Role.name, Role.description, Permission.permission_id)
        .join(RolePermission, RolePermission.role_id == Role.role_id)
        .join(Permission, Permission.permission_id == RolePermission.permission_id)
        .order_by(Role.role_id, Permission.permission_id)
    )
    roles: dict[str, dict] = {}
    for row in result.all():
        item = roles.setdefault(row[0], {"role_id": row[0], "name": row[1], "description": row[2], "permissions": []})
        item["permissions"].append(row[3])
    return list(roles.values())


@router.get("/admin/permissions")
async def admin_permissions(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "user:manage")
    result = await session.execute(select(Permission.permission_id, Permission.description).order_by(Permission.permission_id))
    return [{"permission_id": row[0], "description": row[1]} for row in result.all()]


@router.get("/admin/audit-logs")
async def admin_audit_logs(
    limit: int = 100,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    require_permission(principal, "audit:read")
    result = await session.execute(
        select(AuthAuditLog)
        .where(AuthAuditLog.tenant_id == principal.tenant_id)
        .order_by(AuthAuditLog.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        {
            "audit_id": str(row.audit_id),
            "actor_user_id": row.actor_user_id,
            "action": row.action,
            "outcome": row.outcome,
            "reason": row.reason,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "details": row.details,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in result.scalars()
    ]
