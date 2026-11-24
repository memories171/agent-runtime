"""Tool Result / Artifact reference management.

Small results stay inline; large results are offloaded via the existing
CheckpointSnapshotService object store (SnapshotStore) and referenced by an
artifact id. Artifacts are tenant-scoped with ACL; cross-tenant reads are
rejected before any storage access. Original Run Events are never modified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent_runtime.context.pack import ContextItem, Provenance

DEFAULT_INLINE_LIMIT_CHARS = 2_000


@dataclass
class ArtifactRef:
    artifact_id: str
    tenant_id: str
    object_ref: str
    summary: str
    source_event_id: str | None
    acl: list[str]
    size_bytes: int


def artifact_object_ref(tenant_id: str, run_id: str, artifact_id: str) -> str:
    """Tenant-partitioned object key; validated by LocalSnapshotStore path guard."""
    return f"context-artifacts/{tenant_id}/{run_id}/{artifact_id}.json"


def should_offload(result: Any, limit_chars: int = DEFAULT_INLINE_LIMIT_CHARS) -> bool:
    payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return len(payload) > limit_chars


def summarize_result(result: Any, max_chars: int = 240) -> str:
    payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return payload[:max_chars] + ("..." if len(payload) > max_chars else "")


def check_artifact_access(artifact_tenant_id: str, principal_tenant_id: str, acl: list[str], principal_scopes: set[str], action: str = "read") -> None:
    """Pre-storage ACL gate. Raises PermissionError on any mismatch."""
    if artifact_tenant_id != principal_tenant_id:
        raise PermissionError("artifact tenant mismatch")
    if acl and not (set(acl) & principal_scopes) and "artifact:read:tenant" not in principal_scopes:
        raise PermissionError(f"artifact ACL denies {action}")


def inline_result_item(result: Any, provenance: Provenance, limit_chars: int = DEFAULT_INLINE_LIMIT_CHARS) -> ContextItem:
    payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    value = payload if len(payload) <= limit_chars else payload[:limit_chars] + "..."
    return ContextItem(value=value, provenance=provenance)
