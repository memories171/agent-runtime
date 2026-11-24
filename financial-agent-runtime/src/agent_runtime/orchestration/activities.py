"""Sub-Agent Orchestration 的全部 IO 操作（Temporal Activities）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from temporalio import activity

from agent_runtime.activities_context import _persistence as context_persistence
from agent_runtime.contracts import Principal, WorkflowInput
from agent_runtime.db import RunRecord, append_event, session_for_tenant
from agent_runtime.orchestration.context_projection import ChildContextInput, project_child_context
from agent_runtime.orchestration.delegation_service import (
    DelegationService,
    deterministic_child_run_id,
)
from agent_runtime.orchestration.models import CapabilityGrant
from agent_runtime.orchestration.token_service import CapabilityDenied, CapabilityTokenService


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _child_prompt(root_goal: str, node: dict[str, Any], upstream: list[dict[str, Any]]) -> str:
    lines = [
        f"父任务目标：{root_goal}",
        f"你负责的子任务：{node.get('name', node.get('node_id', 'sub-agent'))}",
        f"子任务说明：{node.get('description', '')}",
        "只处理该子任务；不要执行无关任务，不要自行扩大权限。",
    ]
    for item in upstream or []:
        summary = item.get("output") or {}
        if isinstance(summary, dict):
            summary = summary.get("summary") or summary.get("result") or summary
        lines.append(f"上游节点 {item.get('node_id')}: {str(summary)[:800]}")
    return "\n".join(lines)


async def _build_child_context(payload: dict[str, Any]) -> dict[str, Any]:
    """在 Activity 内读取 Parent Context / Artifact，投影出独立 Child Context 种子。"""
    input_data = ChildContextInput.model_validate(payload["child_context_input"])
    principal = Principal.model_validate(payload["principal"])
    tenant_id = payload["tenant_id"]
    if principal.tenant_id != tenant_id or input_data.tenant_id != tenant_id:
        raise CapabilityDenied("cross_tenant_context_projection")

    persistence = context_persistence
    parent_pack = None
    allowed_artifacts: dict[str, dict[str, Any]] = {}
    async with session_for_tenant(tenant_id) as session:
        for raw_ref in input_data.context_references:
            if raw_ref.get("tenant_id") != tenant_id or raw_ref.get("run_id") != input_data.parent_run_id:
                continue
            pack = await persistence.load_pack(
                session, tenant_id, input_data.parent_run_id,
                str(raw_ref.get("context_pack_id") or ""),
            )
            if pack is not None:
                parent_pack = pack.model_dump(mode="json")
                break

        scopes = set(principal.scopes) | set(principal.permissions)
        for raw in input_data.artifact_refs:
            artifact_id = str((raw or {}).get("artifact_id") or "")
            if not artifact_id:
                continue
            ref = await persistence.get_artifact(
                session,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                principal_tenant_id=principal.tenant_id,
                principal_scopes=scopes,
                allowed_run_ids={input_data.parent_run_id, input_data.child_run_id},
            )
            if ref is not None:
                allowed_artifacts[artifact_id] = {
                    "artifact_id": ref.artifact_id,
                    "tenant_id": ref.tenant_id,
                    "object_ref": ref.object_ref,
                    "summary": ref.summary,
                    "source_event_id": ref.source_event_id,
                    "acl": ref.acl,
                }

        seed = project_child_context(
            input_data, parent_pack=parent_pack, allowed_artifacts=allowed_artifacts,
        )
        await append_event(session, UUID(input_data.child_run_id), tenant_id, "CONTEXT_PROJECTED", {
            "delegation_id": input_data.delegation_id,
            "parent_run_id": input_data.parent_run_id,
            "source_context_pack_ids": seed.source_context_pack_ids,
            "upstream_node_count": len(seed.upstream_nodes),
            "artifact_count": len(seed.artifact_refs),
            "rejected_count": len(seed.rejected_references),
        })
        for rejection in seed.rejected_references:
            await append_event(session, UUID(input_data.child_run_id), tenant_id, "CONTEXT_REFERENCE_REJECTED", {
                "delegation_id": input_data.delegation_id,
                "kind": rejection.kind,
                "ref_id": rejection.ref_id,
                "reason": rejection.reason,
            })
        await append_event(session, UUID(input_data.child_run_id), tenant_id, "CHILD_CONTEXT_CREATED", {
            "delegation_id": input_data.delegation_id,
            "parent_run_id": input_data.parent_run_id,
            "agent_role": input_data.agent_role,
            "schema_version": seed.schema_version,
        })
        await session.commit()
    return seed.model_dump(mode="json")


@activity.defn
async def build_child_context_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _build_child_context(payload)


@activity.defn
async def create_delegation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    tenant_id = payload["tenant_id"]
    parent_run_id = str(payload["parent_run_id"])
    principal = Principal.model_validate(payload["principal"])
    if principal.tenant_id != tenant_id:
        raise CapabilityDenied("cross_tenant_delegation")

    root_token_id = UUID(str(payload["root_token_id"]))
    root_expires_at = _utc(datetime.fromisoformat(payload["root_expires_at"]))
    node = payload["node"]
    attempt = int(payload.get("attempt", 1))
    timeout_seconds = int(node.get("timeout_seconds", 900))
    max_depth = int(payload.get("max_depth", 3))
    max_steps = int(payload.get("max_steps", 8))
    child_agent_id = str(node.get("agent_role") or "executor")
    child_run_id = deterministic_child_run_id(tenant_id, parent_run_id, node["node_id"], attempt)

    await CapabilityTokenService.issue_root(
        principal,
        parent_run_id,
        tenant_id=tenant_id,
        token_id=root_token_id,
        expires_at=root_expires_at,
        max_depth=max_depth,
        ttl_seconds=int(payload.get("max_duration_seconds", 7200)),
    )

    async with session_for_tenant(tenant_id) as session:
        child_run = await session.get(RunRecord, child_run_id)
        if child_run is None:
            session.add(RunRecord(
                run_id=child_run_id,
                tenant_id=tenant_id,
                user_id=principal.user_id,
                session_id=payload.get("session_id") or principal.session_id or "subagent-session",
                agent_id=child_agent_id,
                prompt=_child_prompt(payload.get("root_goal", ""), node, payload.get("upstream") or []),
                run_kind="SUB_AGENT",
                status="QUEUED",
                metadata={
                    "parent_run_id": parent_run_id,
                    "node_id": node["node_id"],
                    "attempt": attempt,
                },
            ))
        elif child_run.tenant_id != tenant_id or child_run.run_kind != "SUB_AGENT":
            raise CapabilityDenied("child_run_conflict")
        await session.commit()

    requested = CapabilityGrant(
        capabilities=set(node.get("required_permissions") or ["agent:execute"]),
        resource_scope={"tenant_id": tenant_id, "resources": "*", "classifications": "*"},
        max_depth=max(1, max_depth - 1),
        timeout_seconds=timeout_seconds,
    )
    delegation, created = await DelegationService.create_delegation(
        tenant_id=tenant_id,
        parent_run_id=parent_run_id,
        parent_token_id=root_token_id,
        requested=requested,
        parent_agent_id=payload.get("parent_agent_id", "parent"),
        child_agent_id=child_agent_id,
        node_id=node["node_id"],
        attempt=attempt,
        graph_id=payload.get("graph_id"),
        child_run_id=child_run_id,
        actor=principal,
    )
    token = await CapabilityTokenService.validate(delegation.capability_token_id, tenant_id)

    context_input = dict(payload.get("child_context") or {})
    context_input.update({
        "tenant_id": tenant_id,
        "parent_run_id": parent_run_id,
        "child_run_id": str(child_run_id),
        "delegation_id": str(delegation.delegation_id),
        "capability_token_id": str(delegation.capability_token_id),
        "agent_role": child_agent_id,
        "task_instruction": _child_prompt(payload.get("root_goal", ""), node, payload.get("upstream") or []),
        "immutable_constraints": list(context_input.get("immutable_constraints") or [payload.get("root_goal", "")]),
    })
    child_context = await _build_child_context({
        "tenant_id": tenant_id,
        "principal": payload["principal"],
        "child_context_input": context_input,
    })

    child_input = WorkflowInput(
        run_id=str(child_run_id),
        tenant_id=tenant_id,
        user_id=principal.user_id,
        session_id=payload.get("session_id") or principal.session_id or "subagent-session",
        agent_id=child_agent_id,
        prompt=_child_prompt(payload.get("root_goal", ""), node, payload.get("upstream") or []),
        scopes=sorted(token.capabilities),
        permissions=sorted(token.capabilities),
        roles=list(principal.roles),
        authentication_method=principal.authentication_method,
        token_expires_at=token.expires_at,
        max_steps=max_steps,
        max_duration_seconds=timeout_seconds,
        skill_ids=[],
        model_name=payload.get("model_name"),
        harness_version=payload.get("harness_version", "dev"),
        context_config={"immutable_constraints": [payload.get("root_goal", "")]},
        graph_config={},
        run_kind="SUB_AGENT",
        parent_run_id=parent_run_id,
        delegation_id=str(delegation.delegation_id),
        capability_token_id=str(delegation.capability_token_id),
        agent_role=child_agent_id,
        child_context=child_context,
    )
    return {
        "delegation": delegation.model_dump(mode="json"),
        "child_input": child_input.model_dump(mode="json"),
        "child_workflow_id": delegation.child_workflow_id,
        "created": created,
    }


@activity.defn
async def mark_delegation_started_activity(payload: dict[str, Any]) -> dict[str, Any]:
    record = await DelegationService.mark_started(payload["tenant_id"], payload["delegation_id"])
    return record.model_dump(mode="json")


@activity.defn
async def mark_delegation_terminal_activity(payload: dict[str, Any]) -> dict[str, Any]:
    record = await DelegationService.mark_terminal(
        payload["tenant_id"],
        payload["delegation_id"],
        payload["status"],
        result_summary=payload.get("result_summary"),
        error=payload.get("error"),
        artifact_refs=payload.get("artifact_refs") or [],
    )
    return record.model_dump(mode="json")


@activity.defn
async def mark_delegation_cancel_requested_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """记录取消请求已发出（非终态投影）；Child 未确认终止前绝不写 CANCELLED。"""
    record, won = await DelegationService.mark_cancel_requested(
        payload["tenant_id"],
        payload["delegation_id"],
        error=payload.get("error"),
    )
    return {"delegation": record.model_dump(mode="json"), "won": won}


@activity.defn
async def settle_delegation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """幂等终态写入：Cancel/Timeout/Complete 并发时先到者胜，不覆盖既有终态。"""
    record, won = await DelegationService.settle_terminal(
        payload["tenant_id"],
        payload["delegation_id"],
        payload["status"],
        result_summary=payload.get("result_summary"),
        error=payload.get("error"),
        artifact_refs=payload.get("artifact_refs") or [],
    )
    return {"delegation": record.model_dump(mode="json"), "won": won}
