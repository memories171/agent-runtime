from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Header, HTTPException, Request, status

from agent_runtime.auth_jwt import verify_jwt
from agent_runtime.config import settings
from agent_runtime.contracts import Principal
from agent_runtime.rls import set_current_tenant, system_session

logger = logging.getLogger("agent_runtime.auth")

# 同一个 token 只记录一次认证成功，避免每次请求都刷审计日志。
_logged_auth_success: set[str] = set()


def _scopes_from_header(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


async def _load_identity(user_id: str, tenant_id: str) -> Principal | None:
    from sqlalchemy import select

    from agent_runtime.db import (
        Role, RolePermission, SessionLocal, TenantMembership, User, UserRole,
    )

    async with system_session(SessionLocal, "identity_resolver") as session:
        user = await session.get(User, user_id)
        if user is None or user.status != "active":
            return None
        now = datetime.now(timezone.utc)
        membership_result = await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user_id,
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
            )
        )
        record = membership_result.scalar_one_or_none()
        if record is None or (record.expires_at is not None and record.expires_at < now):
            return None
        role_result = await session.execute(
            select(Role.name, RolePermission.permission_id)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .join(RolePermission, RolePermission.role_id == Role.role_id)
            .where(UserRole.tenant_id == tenant_id, UserRole.user_id == user_id)
        )
        rows = role_result.all()
        permissions = {row[1] for row in rows}
        roles = {row[0] for row in rows}
        return Principal(
            tenant_id=tenant_id,
            user_id=user_id,
            scopes=set(permissions),
            permissions=set(permissions),
            roles=roles,
            organization_id=record.organization_id,
            authentication_method="oidc",
            token_subject=user_id,
        )


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_scopes: str | None = Header(default=None),
) -> Principal:
    mode = settings.auth_mode.strip().lower()

    if mode == "dev":
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dev Header Auth is disabled in production",
            )
        logger.warning(
            "DEV_HEADER_AUTH used: X-Tenant-ID=%s X-User-ID=%s; never use this outside development",
            x_tenant_id,
            x_user_id,
        )
        if not x_tenant_id or not x_user_id:
            raise HTTPException(status_code=401, detail="X-Tenant-ID and X-User-ID are required")
        scopes = _scopes_from_header(x_scopes)
        principal = Principal(
            tenant_id=x_tenant_id,
            user_id=x_user_id,
            scopes=scopes,
            permissions=set(scopes),
            authentication_method="dev",
        )
        set_current_tenant(principal.tenant_id)
        request.state.principal = principal
        return principal

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = await verify_jwt(token)
    except Exception as exc:
        logger.warning("JWT verification failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc

    user_id = str(claims.get(settings.jwt_user_claim) or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="token missing subject claim")
    requested_tenant = x_tenant_id or str(claims.get(settings.jwt_tenant_claim) or "")
    if not requested_tenant:
        raise HTTPException(status_code=403, detail="tenant context is required")

    principal = await _load_identity(user_id, requested_tenant)
    if principal is None:
        from agent_runtime.authorization.audit import record_auth_event
        from agent_runtime.db import SessionLocal

        async with system_session(SessionLocal, "identity_resolver") as session:
            await record_auth_event(
                session,
                requested_tenant,
                user_id,
                "AUTHENTICATION",
                "DENY",
                reason="active user or tenant membership not found",
                details={"claims_sub": user_id},
            )
            await session.commit()
        raise HTTPException(status_code=403, detail="user is not active in requested tenant")

    expiration = claims.get("exp")
    if isinstance(expiration, (int, float)):
        principal.token_expires_at = datetime.fromtimestamp(expiration, tz=timezone.utc)

    # Authorization is server-side only. Token-supplied roles/permissions are
    # NEVER trusted by default: a signed token must not self-declare elevated
    # access (e.g. "runs:read:tenant"). The DB-resolved identity is authoritative.
    if settings.trust_jwt_authorization_claims:
        claim_roles = claims.get(settings.jwt_roles_claim)
        if isinstance(claim_roles, list):
            principal.roles.update(str(item) for item in claim_roles)
        claim_permissions = claims.get(settings.jwt_permissions_claim)
        if isinstance(claim_permissions, list):
            principal.permissions.update(str(item) for item in claim_permissions)
        principal.scopes = set(principal.permissions)
    else:
        suspicious = [
            name for name in (settings.jwt_roles_claim, settings.jwt_permissions_claim)
            if claims.get(name)
        ]
        if suspicious:
            logger.warning(
                "Ignoring untrusted authorization claims %s from token for %s/%s",
                suspicious, principal.tenant_id, principal.user_id,
            )
        # scopes mirror the DB-resolved permissions exactly; no amplification.
        principal.scopes = set(principal.permissions)

    principal.organization_id = principal.organization_id or str(claims.get(settings.jwt_organization_claim) or "") or None

    from agent_runtime.authorization.audit import record_auth_event
    from agent_runtime.db import SessionLocal

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if token_hash not in _logged_auth_success:
        if len(_logged_auth_success) > 10_000:
            _logged_auth_success.clear()
        async with system_session(SessionLocal, "identity_resolver") as session:
            await record_auth_event(
                session,
                principal.tenant_id,
                principal.user_id,
                "AUTHENTICATION",
                "ALLOW",
                details={
                    "authentication_method": "oidc",
                    "roles": sorted(principal.roles),
                    "token_fingerprint": token_hash[:12],
                },
            )
            await session.commit()
        _logged_auth_success.add(token_hash)
    set_current_tenant(principal.tenant_id)
    request.state.principal = principal
    return principal


def can_access_run(principal: Principal, run_owner_id: str) -> bool:
    return principal.user_id == run_owner_id or "runs:read:tenant" in principal.scopes


def can_manage_run(principal: Principal, run_owner_id: str) -> bool:
    return principal.user_id == run_owner_id or "runs:manage:tenant" in principal.scopes


def can_cancel_run(principal: Principal, run_owner_id: str) -> bool:
    """Risk may stop any tenant Run without gaining full Run management."""
    return (
        can_manage_run(principal, run_owner_id)
        or "runs:cancel:tenant" in principal.scopes
    )


def require_permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions and permission not in principal.scopes:
        raise HTTPException(status_code=403, detail=f"permission required: {permission}")
