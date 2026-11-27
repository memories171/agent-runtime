"""ContextManager persistence facade.

All DB / object-store access for the Context Manager lives here and is invoked
from Temporal activities (never from workflow code). Enforces tenant scoping on
every query. ContextPacks are reconstructed from persisted JSON plus the
Checkpoint's event cursor after a worker restart.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.context.artifacts import ArtifactRef, artifact_object_ref, check_artifact_access
from agent_runtime.context.pack import CONTEXT_PACK_SCHEMA_VERSION, ContextPack
from agent_runtime.context.summary import (
    SUMMARY_SYSTEM_PROMPT,
    SummaryRequest,
    content_hash,
    summary_idempotency_key,
)


class ContextPersistence:
    def __init__(self, store) -> None:
        self._store = store

    async def save_pack(self, session: AsyncSession, pack: ContextPack) -> None:
        from agent_runtime.db import ContextPackRecord

        metadata = pack.metadata
        row = ContextPackRecord(
            tenant_id=metadata.tenant_id,
            run_id=metadata.run_id,
            context_pack_id=metadata.context_pack_id,
            schema_version=metadata.schema_version,
            model_name=metadata.model_name,
            event_cursor=metadata.event_cursor,
            pack_json=pack.model_dump(mode="json"),
            compression_count=metadata.compression_count,
            compression_ratio=metadata.compression_ratio,
            degraded=metadata.degraded,
            degradation_reason=metadata.degradation_reason,
        )
        session.add(row)
        await session.flush()

    async def load_pack(
        self, session: AsyncSession, tenant_id: str, run_id: str, context_pack_id: str | None = None
    ) -> ContextPack | None:
        from agent_runtime.db import ContextPackRecord

        query = select(ContextPackRecord).where(
            ContextPackRecord.tenant_id == tenant_id,
            ContextPackRecord.run_id == run_id,
        )
        if context_pack_id:
            query = query.where(ContextPackRecord.context_pack_id == context_pack_id)
        query = query.order_by(ContextPackRecord.created_at.desc()).limit(1)
        row = (await session.execute(query)).scalars().first()
        if row is None:
            return None
        return ContextPack.model_validate(row.pack_json)

    async def list_packs(
        self, session: AsyncSession, tenant_id: str, run_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        from agent_runtime.db import ContextPackRecord

        rows = (
            await session.execute(
                select(ContextPackRecord)
                .where(ContextPackRecord.tenant_id == tenant_id, ContextPackRecord.run_id == run_id)
                .order_by(ContextPackRecord.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "context_pack_id": r.context_pack_id,
                "schema_version": r.schema_version,
                "model_name": r.model_name,
                "event_cursor": r.event_cursor,
                "compression_count": r.compression_count,
                "compression_ratio": r.compression_ratio,
                "degraded": r.degraded,
                "degradation_reason": r.degradation_reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def save_summary(
        self, session: AsyncSession, request: SummaryRequest, summary_content: str, key_facts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Insert a summary idempotently. Returns the persisted summary record."""
        from agent_runtime.db import ContextSummaryRecord

        key = summary_idempotency_key(
            request.tenant_id, request.run_id, request.source_event_end, request.version,
            source_event_ids=request.source_event_ids,
            events=request.events,
            model_version=request.model_version,
            prompt_version=SUMMARY_SYSTEM_PROMPT,
        )
        existing = (
            await session.execute(
                select(ContextSummaryRecord).where(
                    ContextSummaryRecord.tenant_id == request.tenant_id,
                    ContextSummaryRecord.idempotency_key == key,
                )
            )
        ).scalars().first()
        if existing is not None:
            return {
                "summary_id": str(existing.summary_id),
                "summary_content": existing.summary_content,
                "summary_version": existing.summary_version,
                "content_hash": existing.content_hash,
                "key_facts": existing.key_facts,
                "source_event_start": existing.source_event_start,
                "source_event_end": existing.source_event_end,
                "idempotent_hit": True,
            }
        digest = content_hash(summary_content)
        row = ContextSummaryRecord(
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            idempotency_key=key,
            source_event_start=request.source_event_start,
            source_event_end=request.source_event_end,
            source_event_ids=request.source_event_ids,
            summary_version=request.version,
            summary_content=summary_content,
            model_version=request.model_version,
            content_hash=digest,
            key_facts=key_facts,
        )
        session.add(row)
        await session.flush()
        return {
            "summary_id": str(row.summary_id),
            "summary_content": row.summary_content,
            "summary_version": row.summary_version,
            "content_hash": row.content_hash,
            "key_facts": row.key_facts,
            "source_event_start": row.source_event_start,
            "source_event_end": row.source_event_end,
            "idempotent_hit": False,
        }

    async def list_summaries(
        self, session: AsyncSession, tenant_id: str, run_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        from agent_runtime.db import ContextSummaryRecord

        rows = (
            await session.execute(
                select(ContextSummaryRecord)
                .where(ContextSummaryRecord.tenant_id == tenant_id, ContextSummaryRecord.run_id == run_id)
                .order_by(ContextSummaryRecord.summary_version.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "summary_id": str(r.summary_id),
                "summary_version": r.summary_version,
                "source_event_start": r.source_event_start,
                "source_event_end": r.source_event_end,
                "source_event_ids": r.source_event_ids,
                "summary_content": r.summary_content,
                "content_hash": r.content_hash,
                "model_version": r.model_version,
                "key_facts": r.key_facts,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def put_artifact(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        run_id: str,
        result: dict[str, Any],
        source_event_id: str | None,
        acl: list[str],
        summary: str,
    ) -> ArtifactRef:
        from agent_runtime.db import ContextArtifactRecord

        artifact_id = str(uuid4())
        object_ref = artifact_object_ref(tenant_id, run_id, artifact_id)
        payload = json.dumps(result, ensure_ascii=False, default=str).encode()
        await self._store.put(object_ref, payload)
        row = ContextArtifactRecord(
            tenant_id=tenant_id,
            run_id=run_id,
            object_ref=object_ref,
            summary=summary,
            source_event_id=source_event_id,
            acl=acl,
            size_bytes=len(payload),
        )
        session.add(row)
        await session.flush()
        return ArtifactRef(
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            object_ref=object_ref,
            summary=summary,
            source_event_id=source_event_id,
            acl=acl,
            size_bytes=len(payload),
        )

    async def get_artifact(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        artifact_id: str,
        principal_tenant_id: str,
        principal_scopes: set[str],
        allowed_run_ids: set[str] | None = None,
    ) -> ArtifactRef | None:
        from agent_runtime.db import ContextArtifactRecord

        row = (
            await session.execute(
                select(ContextArtifactRecord).where(
                    ContextArtifactRecord.tenant_id == tenant_id,
                    ContextArtifactRecord.artifact_id == artifact_id,
                )
            )
        ).scalars().first()
        if row is None:
            return None
        if allowed_run_ids is not None and str(row.run_id) not in set(allowed_run_ids):
            return None
        check_artifact_access(row.tenant_id, principal_tenant_id, row.acl, principal_scopes)
        return ArtifactRef(
            artifact_id=str(row.artifact_id),
            tenant_id=row.tenant_id,
            object_ref=row.object_ref,
            summary=row.summary,
            source_event_id=row.source_event_id,
            acl=row.acl,
            size_bytes=row.size_bytes,
        )

    async def artifact_payload(self, ref: ArtifactRef) -> dict[str, Any]:
        raw = await self._store.get(ref.object_ref)
        return json.loads(raw)
