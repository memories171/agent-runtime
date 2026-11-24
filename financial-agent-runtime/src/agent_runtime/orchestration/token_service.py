"""Capability Token 签发、衰减、校验与撤销（所有状态来自数据库，遵循 Tenant RLS）。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from agent_runtime.contracts import Principal
from agent_runtime.db import CapabilityTokenRecord as TokenRow, session_for_tenant
from agent_runtime.orchestration.models import CapabilityGrant, CapabilityTokenRecord

WILDCARD = "*"
MAX_CHAIN_DEPTH = 8
DEFAULT_ROOT_TTL_SECONDS = 7200
DEFAULT_MAX_DEPTH = 3


class CapabilityDenied(PermissionError):
    """不可重试的权限失败；消息包含 capability_denied 标记。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        detail = f"capability_denied: {code}"
        if message:
            detail = f"{detail}: {message}"
        super().__init__(detail)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _principal_permissions(principal: Principal) -> set[str]:
    return set(principal.permissions) | set(principal.scopes)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _scope_contains(parent: Any, child: Any) -> bool:
    """判断 child resource_scope 是否被 parent 允许。"""
    if child == WILDCARD:
        return parent == WILDCARD
    if isinstance(child, dict):
        if not isinstance(parent, dict):
            return False
        return all(key in parent and _scope_contains(parent[key], value) for key, value in child.items())
    if isinstance(child, (list, tuple, set, frozenset)):
        if parent == WILDCARD:
            return True
        if not isinstance(parent, (list, tuple, set, frozenset)):
            return False
        if WILDCARD in parent:
            return True
        return all(item in parent for item in child)
    if parent == WILDCARD:
        return True
    return parent == child


def _signature(
    *,
    token_id: UUID,
    tenant_id: str,
    run_id: UUID,
    parent_token_id: UUID | None,
    subject_run_id: UUID | None,
    subject_agent_id: str | None,
    capabilities: set[str],
    resource_scope: dict[str, Any],
    depth: int,
    max_depth: int,
    expires_at: datetime,
) -> str:
    payload = {
        "token_id": str(token_id),
        "tenant_id": tenant_id,
        "run_id": str(run_id),
        "parent_token_id": str(parent_token_id) if parent_token_id else None,
        "subject_run_id": str(subject_run_id) if subject_run_id else None,
        "subject_agent_id": subject_agent_id,
        "capabilities": sorted(capabilities),
        "resource_scope": _jsonable(resource_scope),
        "depth": depth,
        "max_depth": max_depth,
        "expires_at": _utc(expires_at).isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_to_record(row: TokenRow) -> CapabilityTokenRecord:
    return CapabilityTokenRecord(
        token_id=row.token_id,
        tenant_id=row.tenant_id,
        run_id=row.run_id,
        parent_token_id=row.parent_token_id,
        subject_run_id=row.subject_run_id,
        subject_agent_id=row.subject_agent_id,
        capabilities=set(row.capabilities or []),
        resource_scope=dict(row.resource_scope or {}),
        depth=row.depth,
        max_depth=row.max_depth,
        expires_at=_utc(row.expires_at),
        revoked_at=_utc(row.revoked_at) if row.revoked_at else None,
        signature_hash=row.signature_hash,
        created_at=_utc(row.created_at) if row.created_at else None,
    )


def _assert_active(record: CapabilityTokenRecord, at: datetime) -> None:
    if record.revoked_at is not None:
        raise CapabilityDenied("token_revoked", str(record.token_id))
    if _utc(record.expires_at) <= at:
        raise CapabilityDenied("token_expired", str(record.token_id))
    if record.depth > record.max_depth:
        raise CapabilityDenied("depth_exceeded", str(record.token_id))


def _assert_scope_allowed(parent_scope: dict[str, Any], child_scope: dict[str, Any]) -> None:
    if not _scope_contains(parent_scope, child_scope):
        raise CapabilityDenied("resource_scope_exceeded")


async def _load(session, tenant_id: str, token_id: UUID) -> TokenRow:
    row = (await session.execute(
        select(TokenRow).where(TokenRow.tenant_id == tenant_id, TokenRow.token_id == token_id)
    )).scalar_one_or_none()
    if row is None:
        raise CapabilityDenied("token_not_found", str(token_id))
    return row


async def _assert_chain_active(session, row: TokenRow, at: datetime) -> None:
    """撤销或过期父 Token 会立即影响所有后代。"""
    seen: set[UUID] = set()
    current = row
    for _ in range(MAX_CHAIN_DEPTH):
        if current.token_id in seen:
            raise CapabilityDenied("token_chain_cycle")
        seen.add(current.token_id)
        _assert_active(_row_to_record(current), at)
        if current.parent_token_id is None:
            return
        current = await _load(session, current.tenant_id, current.parent_token_id)
    raise CapabilityDenied("token_chain_too_deep")


class CapabilityTokenService:
    @staticmethod
    async def issue_root(
        principal: Principal,
        run_id: UUID | str,
        *,
        tenant_id: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        ttl_seconds: int | None = DEFAULT_ROOT_TTL_SECONDS,
        resource_scope: dict[str, Any] | None = None,
        token_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> CapabilityTokenRecord:
        tenant = tenant_id or principal.tenant_id
        if tenant != principal.tenant_id:
            raise CapabilityDenied("cross_tenant_issuance")
        if max_depth < 1:
            raise CapabilityDenied("invalid_max_depth")
        now = _now()
        expiry = _utc(expires_at) if expires_at else now + timedelta(seconds=int(ttl_seconds or DEFAULT_ROOT_TTL_SECONDS))
        if principal.token_expires_at is not None:
            expiry = min(expiry, _utc(principal.token_expires_at))
        if expiry <= now:
            raise CapabilityDenied("principal_token_expired")

        capabilities = _principal_permissions(principal)
        scope = resource_scope or {
            "tenant_id": tenant,
            "resources": WILDCARD,
            "classifications": WILDCARD,
        }
        if scope.get("tenant_id", tenant) != tenant:
            raise CapabilityDenied("cross_tenant_scope")
        new_id = token_id or uuid4()

        async with session_for_tenant(tenant) as session:
            existing = (await session.execute(
                select(TokenRow).where(TokenRow.tenant_id == tenant, TokenRow.token_id == new_id)
            )).scalar_one_or_none()
            if existing is not None:
                record = _row_to_record(existing)
                expected = _signature(
                    token_id=new_id, tenant_id=tenant, run_id=UUID(str(run_id)),
                    parent_token_id=None, subject_run_id=UUID(str(run_id)),
                    subject_agent_id="root", capabilities=capabilities,
                    resource_scope=scope, depth=0, max_depth=max_depth, expires_at=expiry,
                )
                if record.signature_hash != expected:
                    raise CapabilityDenied("token_id_conflict")
                return record
            signature = _signature(
                token_id=new_id, tenant_id=tenant, run_id=UUID(str(run_id)),
                parent_token_id=None, subject_run_id=UUID(str(run_id)),
                subject_agent_id="root", capabilities=capabilities,
                resource_scope=scope, depth=0, max_depth=max_depth, expires_at=expiry,
            )
            session.add(TokenRow(
                token_id=new_id, tenant_id=tenant, run_id=UUID(str(run_id)),
                parent_token_id=None, subject_run_id=UUID(str(run_id)),
                subject_agent_id="root", capabilities=sorted(capabilities),
                resource_scope=scope, depth=0, max_depth=max_depth,
                expires_at=expiry, revoked_at=None, signature_hash=signature,
            ))
            await session.commit()
        return CapabilityTokenRecord(
            token_id=new_id, tenant_id=tenant, run_id=UUID(str(run_id)),
            subject_run_id=UUID(str(run_id)), subject_agent_id="root",
            capabilities=capabilities, resource_scope=scope, depth=0, max_depth=max_depth,
            expires_at=expiry, signature_hash=signature,
        )

    @staticmethod
    async def attenuate(
        parent_token_id: UUID | str,
        requested: CapabilityGrant,
        *,
        child_run_id: UUID | str,
        subject_agent_id: str,
        tenant_id: str,
        actor: Principal | None = None,
        token_id: UUID | None = None,
    ) -> CapabilityTokenRecord:
        if actor is not None:
            if actor.tenant_id != tenant_id:
                raise CapabilityDenied("cross_tenant_delegation")
            if "agent:execute" not in _principal_permissions(actor):
                raise CapabilityDenied("delegation_permission_required")
            if not requested.capabilities.issubset(_principal_permissions(actor)):
                raise CapabilityDenied("actor_capability_exceeded")
        now = _now()
        async with session_for_tenant(tenant_id) as session:
            parent_row = await _load(session, tenant_id, UUID(str(parent_token_id)))
            await _assert_chain_active(session, parent_row, now)
            parent = _row_to_record(parent_row)
            child_depth = parent.depth + 1
            if child_depth > parent.max_depth:
                raise CapabilityDenied("max_depth_exceeded")
            if requested.max_depth < 1 or requested.max_depth > parent.max_depth:
                raise CapabilityDenied("invalid_child_max_depth")
            if not requested.capabilities.issubset(parent.capabilities):
                raise CapabilityDenied("capability_escalation")
            _assert_scope_allowed(parent.resource_scope, requested.resource_scope)

            expires_at = min(_utc(parent.expires_at), now + timedelta(seconds=requested.timeout_seconds))
            if expires_at <= now:
                raise CapabilityDenied("child_token_expired")
            child_max_depth = min(parent.max_depth, requested.max_depth)
            new_id = token_id or uuid4()
            existing = (await session.execute(
                select(TokenRow).where(TokenRow.tenant_id == tenant_id, TokenRow.token_id == new_id)
            )).scalar_one_or_none()
            if existing is not None:
                record = _row_to_record(existing)
                if record.signature_hash != _signature(
                    token_id=new_id, tenant_id=tenant_id, run_id=parent.run_id,
                    parent_token_id=parent.token_id, subject_run_id=UUID(str(child_run_id)),
                    subject_agent_id=subject_agent_id, capabilities=requested.capabilities,
                    resource_scope=requested.resource_scope, depth=child_depth,
                    max_depth=child_max_depth, expires_at=expires_at,
                ):
                    raise CapabilityDenied("token_id_conflict")
                return record

            signature = _signature(
                token_id=new_id, tenant_id=tenant_id, run_id=parent.run_id,
                parent_token_id=parent.token_id, subject_run_id=UUID(str(child_run_id)),
                subject_agent_id=subject_agent_id, capabilities=requested.capabilities,
                resource_scope=requested.resource_scope, depth=child_depth,
                max_depth=child_max_depth, expires_at=expires_at,
            )
            session.add(TokenRow(
                token_id=new_id, tenant_id=tenant_id, run_id=parent.run_id,
                parent_token_id=parent.token_id, subject_run_id=UUID(str(child_run_id)),
                subject_agent_id=subject_agent_id, capabilities=sorted(requested.capabilities),
                resource_scope=requested.resource_scope, depth=child_depth,
                max_depth=child_max_depth, expires_at=expires_at, revoked_at=None,
                signature_hash=signature,
            ))
            await session.commit()
        return CapabilityTokenRecord(
            token_id=new_id, tenant_id=tenant_id, run_id=parent.run_id,
            parent_token_id=parent.token_id, subject_run_id=UUID(str(child_run_id)),
            subject_agent_id=subject_agent_id, capabilities=set(requested.capabilities),
            resource_scope=dict(requested.resource_scope), depth=child_depth,
            max_depth=child_max_depth, expires_at=expires_at, signature_hash=signature,
        )

    @staticmethod
    async def validate(
        token_id: UUID | str,
        tenant_id: str,
        *,
        required_capabilities: set[str] | None = None,
        resource_scope: dict[str, Any] | None = None,
        subject_run_id: UUID | str | None = None,
        actor: Principal | None = None,
        at: datetime | None = None,
    ) -> CapabilityTokenRecord:
        now = _utc(at) if at else _now()
        async with session_for_tenant(tenant_id) as session:
            row = await _load(session, tenant_id, UUID(str(token_id)))
            if row.tenant_id != tenant_id:
                raise CapabilityDenied("cross_tenant_token")
            await _assert_chain_active(session, row, now)
            record = _row_to_record(row)

        if subject_run_id is not None and record.subject_run_id != UUID(str(subject_run_id)):
            raise CapabilityDenied("subject_run_mismatch")
        if required_capabilities and not set(required_capabilities).issubset(record.capabilities):
            raise CapabilityDenied("required_capability_missing")
        if resource_scope is not None:
            _assert_scope_allowed(record.resource_scope, resource_scope)
        if actor is not None:
            if actor.tenant_id != tenant_id:
                raise CapabilityDenied("cross_tenant_actor")
            if not record.capabilities.issubset(_principal_permissions(actor)):
                raise CapabilityDenied("principal_capability_narrowed")
        expected = _signature(
            token_id=record.token_id, tenant_id=record.tenant_id, run_id=record.run_id,
            parent_token_id=record.parent_token_id, subject_run_id=record.subject_run_id,
            subject_agent_id=record.subject_agent_id, capabilities=record.capabilities,
            resource_scope=record.resource_scope, depth=record.depth,
            max_depth=record.max_depth, expires_at=record.expires_at,
        )
        if record.signature_hash != expected:
            raise CapabilityDenied("signature_mismatch")
        return record

    @staticmethod
    async def revoke(
        token_id: UUID | str,
        tenant_id: str,
        *,
        actor: Principal | None = None,
        reason: str | None = None,
    ) -> CapabilityTokenRecord:
        if actor is not None:
            if actor.tenant_id != tenant_id:
                raise CapabilityDenied("cross_tenant_revocation")
            if "runs:manage:tenant" not in _principal_permissions(actor) and "agent:execute" not in _principal_permissions(actor):
                raise CapabilityDenied("revocation_permission_required")
        async with session_for_tenant(tenant_id) as session:
            row = await _load(session, tenant_id, UUID(str(token_id)))
            if row.revoked_at is None:
                row.revoked_at = _now()
            await session.commit()
            return _row_to_record(row)
