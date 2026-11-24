"""Child Context Projection：从明确授权的输入构建独立上下文种子（纯函数，无 IO）。"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ContextReference(BaseModel):
    context_pack_id: str
    run_id: str
    tenant_id: str
    label: str = ""
    include_summary: bool = True


class ChildContextInput(BaseModel):
    tenant_id: str
    parent_run_id: str
    child_run_id: str
    delegation_id: str
    capability_token_id: str
    agent_role: str
    task_instruction: str
    task_parameters: dict[str, Any] = Field(default_factory=dict)
    context_references: list[dict[str, Any]] = Field(default_factory=list)
    upstream_nodes: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    immutable_constraints: list[str] = Field(default_factory=list)


class ContextProjectionRejection(BaseModel):
    kind: str
    ref_id: str
    reason: str


class ChildContextSeed(BaseModel):
    schema_version: str = "child-context-v1"
    tenant_id: str
    parent_run_id: str
    child_run_id: str
    delegation_id: str
    capability_token_id: str
    agent_role: str
    task_instruction: str
    task_parameters: dict[str, Any] = Field(default_factory=dict)
    immutable_constraints: list[str] = Field(default_factory=list)
    parent_summary: dict[str, Any] | None = None
    upstream_nodes: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_context_pack_ids: list[str] = Field(default_factory=list)
    rejected_references: list[ContextProjectionRejection] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_UPSTREAM_FIELDS = {
    "node_id", "name", "status", "output_summary", "artifact_refs",
    "source_event_ids", "attempts", "error", "token_usage",
}
_ARTIFACT_FIELDS = {"artifact_id", "object_ref", "summary", "source_event_id", "acl"}


def _values(items: list[Any] | None) -> list[Any]:
    return [item.get("value") if isinstance(item, dict) and "value" in item else item for item in (items or [])]


def _parent_summary(pack: dict[str, Any]) -> dict[str, Any]:
    metadata = pack.get("metadata") or {}
    goal = pack.get("goal") or {}
    historical = pack.get("historical") or {}
    return {
        "context_pack_id": metadata.get("context_pack_id"),
        "run_id": metadata.get("run_id"),
        "original_user_goal": (goal.get("original_user_goal") or {}).get("value"),
        "acceptance_criteria": _values(goal.get("acceptance_criteria")),
        "immutable_constraints": _values(goal.get("immutable_constraints")),
        "rolling_summary": (historical.get("rolling_summary") or {}).get("value"),
    }


def project_child_context(
    input_data: ChildContextInput,
    *,
    parent_pack: dict[str, Any] | None,
    allowed_artifacts: dict[str, dict[str, Any]],
) -> ChildContextSeed:
    """按显式引用投影 Child 上下文；失败引用只记录拒绝原因，不抛异常中断 Workflow。"""
    rejections: list[ContextProjectionRejection] = []
    source_ids: list[str] = []
    parent_summary: dict[str, Any] | None = None

    for raw in input_data.context_references:
        try:
            ref = ContextReference.model_validate(raw)
        except Exception:
            rejections.append(ContextProjectionRejection(
                kind="context_pack", ref_id=str((raw or {}).get("context_pack_id", "")), reason="invalid_reference",
            ))
            continue
        if ref.tenant_id != input_data.tenant_id:
            rejections.append(ContextProjectionRejection(
                kind="context_pack", ref_id=ref.context_pack_id, reason="cross_tenant_reference",
            ))
            continue
        if ref.run_id != input_data.parent_run_id:
            rejections.append(ContextProjectionRejection(
                kind="context_pack", ref_id=ref.context_pack_id, reason="run_ownership_mismatch",
            ))
            continue
        if parent_pack is None or (parent_pack.get("metadata") or {}).get("context_pack_id") != ref.context_pack_id:
            rejections.append(ContextProjectionRejection(
                kind="context_pack", ref_id=ref.context_pack_id, reason="context_pack_unavailable",
            ))
            continue
        source_ids.append(ref.context_pack_id)
        if ref.include_summary and parent_summary is None:
            parent_summary = copy.deepcopy(_parent_summary(parent_pack))

    upstream: list[dict[str, Any]] = []
    for item in input_data.upstream_nodes:
        clean = copy.deepcopy({key: value for key, value in item.items() if key in _UPSTREAM_FIELDS})
        if clean.get("node_id"):
            upstream.append(clean)

    artifacts: list[dict[str, Any]] = []
    for raw in input_data.artifact_refs:
        artifact_id = str((raw or {}).get("artifact_id") or "")
        row = allowed_artifacts.get(artifact_id)
        if row is None:
            rejections.append(ContextProjectionRejection(
                kind="artifact", ref_id=artifact_id, reason="artifact_not_allowed",
            ))
            continue
        if row.get("tenant_id") != input_data.tenant_id:
            rejections.append(ContextProjectionRejection(
                kind="artifact", ref_id=artifact_id, reason="cross_tenant_artifact",
            ))
            continue
        if str(row.get("run_id")) not in {input_data.parent_run_id, input_data.child_run_id}:
            rejections.append(ContextProjectionRejection(
                kind="artifact", ref_id=artifact_id, reason="artifact_run_mismatch",
            ))
            continue
        merged = {**row, **raw}
        artifacts.append(copy.deepcopy({
            key: value for key, value in merged.items() if key in _ARTIFACT_FIELDS
        }))

    return ChildContextSeed(
        tenant_id=input_data.tenant_id,
        parent_run_id=input_data.parent_run_id,
        child_run_id=input_data.child_run_id,
        delegation_id=input_data.delegation_id,
        capability_token_id=input_data.capability_token_id,
        agent_role=input_data.agent_role,
        task_instruction=copy.deepcopy(input_data.task_instruction),
        task_parameters=copy.deepcopy(input_data.task_parameters),
        immutable_constraints=copy.deepcopy(input_data.immutable_constraints),
        parent_summary=parent_summary,
        upstream_nodes=upstream,
        artifact_refs=artifacts,
        source_context_pack_ids=source_ids,
        rejected_references=rejections,
    )
