from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.contracts import MemoryRead, Principal
from agent_runtime.db import MemoryRecord


def _meta(record: MemoryRecord) -> dict[str, Any]:
    value = record.metadata_json or {}
    return value if isinstance(value, dict) else {}


def _meta_value(record: MemoryRecord, key: str, default: Any = None) -> Any:
    return _meta(record).get(key, default)


def _updated_at(record: MemoryRecord) -> str:
    value = _meta_value(record, "updated_at")
    if isinstance(value, str):
        return value
    if record.created_at is not None:
        return record.created_at.isoformat()
    return datetime.now(timezone.utc).isoformat()


def to_memory_read(record: MemoryRecord) -> MemoryRead:
    memory_type = record.memory_type
    return MemoryRead(
        memory_id=record.memory_id,
        scope=record.scope,
        memory_type=memory_type,
        type=memory_type.upper(),
        content=record.content,
        title=str(_meta_value(record, "title") or ""),
        owner=record.owner_id or "tenant",
        source_run_id=str(record.source_run_id) if record.source_run_id else None,
        confidence=float(_meta_value(record, "confidence", 0.8)),
        created_at=record.created_at.isoformat() if record.created_at else datetime.now(timezone.utc).isoformat(),
        updated_at=_updated_at(record),
        expires_at=_meta_value(record, "expires_at"),
        acl=list(_meta_value(record, "acl", []) or []),
        archived=bool(_meta_value(record, "archived", False)),
    )


def _visible_conditions(principal: Principal):
    conditions = [(MemoryRecord.scope == "user") & (MemoryRecord.owner_id == principal.user_id)]
    if "memory:read:team" in principal.scopes or "memory:read:tenant" in principal.scopes:
        conditions.append(MemoryRecord.scope == "team")
    if "memory:read:organization" in principal.scopes or "memory:read:tenant" in principal.scopes:
        conditions.append(MemoryRecord.scope == "organization")
    return or_(*conditions)


def _can_manage(principal: Principal, record: MemoryRecord) -> bool:
    if "memory:write:tenant" in principal.scopes:
        return True
    if record.owner_id is not None:
        return record.owner_id == principal.user_id
    acl = list(_meta_value(record, "acl", []) or [])
    return f"user:{principal.user_id}" in acl


async def create_memory(
    session: AsyncSession,
    principal: Principal,
    content: str,
    scope: str,
    memory_type: str,
    source_run_id=None,
    metadata: dict | None = None,
    title: str = "",
    confidence: float = 0.8,
    expires_at: str | None = None,
    acl: list[str] | None = None,
) -> MemoryRecord:
    memory_type = memory_type.lower()
    if memory_type not in {"working", "episodic", "semantic", "procedural"}:
        raise ValueError("unsupported memory type")
    if scope == "organization" and not ({"memory:write:organization", "memory:write:tenant"} & principal.scopes):
        raise PermissionError("organization memory write scope is required")
    if scope == "team" and not ({"memory:write:team", "memory:write:tenant"} & principal.scopes):
        raise PermissionError("team memory write scope is required")
    if scope == "user" and "memory:write" not in principal.scopes and "memory:write:tenant" not in principal.scopes:
        raise PermissionError("memory:write scope is required")
    now = datetime.now(timezone.utc).isoformat()
    merged = dict(metadata or {})
    merged.update({
        "title": title or content[:80],
        "confidence": confidence,
        "expires_at": expires_at,
        "acl": acl or ([f"user:{principal.user_id}"] if scope == "user" else [f"scope:{scope}"]),
        "archived": False,
        "updated_at": now,
    })
    record = MemoryRecord(
        tenant_id=principal.tenant_id,
        owner_id=principal.user_id if scope == "user" else None,
        scope=scope,
        memory_type=memory_type,
        content=content,
        source_run_id=source_run_id,
        metadata_json=merged,
    )
    session.add(record)
    await session.commit()
    return record


async def search_memories(
    session: AsyncSession, principal: Principal, query: str, limit: int = 10
) -> list[MemoryRecord]:
    return await list_memories(session, principal, query=query, memory_type=None, scope=None, include_archived=False, limit=limit)


async def list_memories(
    session: AsyncSession,
    principal: Principal,
    query: str | None = None,
    memory_type: str | None = None,
    scope: str | None = None,
    include_archived: bool = False,
    limit: int = 100,
) -> list[MemoryRecord]:
    stmt = select(MemoryRecord).where(
        MemoryRecord.tenant_id == principal.tenant_id,
        _visible_conditions(principal),
    )
    if memory_type:
        stmt = stmt.where(MemoryRecord.memory_type == memory_type.lower())
    if scope:
        stmt = stmt.where(MemoryRecord.scope == scope)
    rows = list((await session.execute(stmt.order_by(MemoryRecord.created_at.desc()).limit(500))).scalars())
    if not include_archived:
        rows = [row for row in rows if not bool(_meta_value(row, "archived", False))]
    terms = [term.lower() for term in (query or "").split() if term.strip()]
    if terms:
        rows = [
            row for row in rows
            if any(term in f"{_meta_value(row, 'title', '')} {row.content}".lower() for term in terms)
        ]
    return rows[:limit]


async def get_memory(session: AsyncSession, principal: Principal, memory_id: UUID) -> MemoryRecord | None:
    result = await session.execute(
        select(MemoryRecord).where(
            MemoryRecord.memory_id == memory_id,
            MemoryRecord.tenant_id == principal.tenant_id,
            _visible_conditions(principal),
        )
    )
    return result.scalar_one_or_none()


async def update_memory(
    session: AsyncSession,
    principal: Principal,
    record: MemoryRecord,
    *,
    title: str | None,
    content: str | None,
    memory_type: str | None,
    scope: str | None,
    confidence: float | None,
    expires_at: str | None,
    acl: list[str] | None,
) -> MemoryRecord:
    if not _can_manage(principal, record):
        raise PermissionError("current user cannot update this memory")
    if scope and scope != record.scope:
        if scope == "organization" and not ({"memory:write:organization", "memory:write:tenant"} & principal.scopes):
            raise PermissionError("organization memory write scope is required")
        if scope == "team" and not ({"memory:write:team", "memory:write:tenant"} & principal.scopes):
            raise PermissionError("team memory write scope is required")
        record.scope = scope
    if content is not None:
        record.content = content
    if memory_type:
        normalized = memory_type.lower()
        if normalized not in {"working", "episodic", "semantic", "procedural"}:
            raise ValueError("unsupported memory type")
        record.memory_type = normalized
    metadata = dict(_meta(record))
    if title is not None:
        metadata["title"] = title
    if confidence is not None:
        metadata["confidence"] = confidence
    metadata["expires_at"] = expires_at
    if acl is not None:
        metadata["acl"] = acl
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    record.metadata_json = metadata
    await session.commit()
    return record


async def archive_memory(session: AsyncSession, principal: Principal, record: MemoryRecord) -> MemoryRecord:
    if not _can_manage(principal, record):
        raise PermissionError("current user cannot archive this memory")
    metadata = dict(_meta(record))
    metadata["archived"] = True
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    record.metadata_json = metadata
    await session.commit()
    return record


async def delete_memory(session: AsyncSession, principal: Principal, record: MemoryRecord) -> None:
    if not _can_manage(principal, record):
        raise PermissionError("current user cannot delete this memory")
    await session.delete(record)
    await session.commit()
