"""Temporal activities for the Context Manager.

All non-deterministic work (LLM summary, DB reads/writes, object storage) is
isolated here. Workflow code only passes resolved plain data and calls the
deterministic assembler through these activities.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from temporalio import activity

from agent_runtime.agent.model import model_adapter
from agent_runtime.context.assembly import (
    AssemblyInput,
    ContextOverflowError,
    assemble,
    pack_to_model_payload,
)
from agent_runtime.context.artifacts import should_offload, summarize_result
from agent_runtime.context.service import ContextPersistence
from agent_runtime.context.summary import (
    SummaryRequest,
    build_summary_input,
    safe_truncate_events,
    validate_summary,
)
from agent_runtime.context.tokenizer import get_estimator

from agent_runtime.checkpoint.storage import build_snapshot_store
from agent_runtime.config import settings
from agent_runtime.db import RunEvent, append_event, session_for_tenant


_persistence = ContextPersistence(build_snapshot_store(settings))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(details: dict[str, Any]) -> None:
    """Heartbeat when running inside a Temporal activity; no-op in unit tests."""
    try:
        activity.heartbeat(details)
    except RuntimeError:
        pass


def _normalized_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = list(payload.get("events") or [])
    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(events):
        if not isinstance(raw, dict):
            continue
        normalized.append(
            {
                "sequence": int(raw.get("sequence", idx)),
                "event_id": str(raw.get("event_id", "")),
                "event_type": str(raw.get("event_type", "EVENT")),
                "payload": raw.get("payload", raw),
                "relevance_score": float(raw.get("relevance_score", 0.5)),
            }
        )
    return normalized


def _build_assembly_input(payload: dict[str, Any], summary: dict[str, Any] | None) -> AssemblyInput:
    return AssemblyInput(
        run_id=payload["run_id"],
        tenant_id=payload["tenant_id"],
        agent_id=payload.get("agent_id", "default-agent"),
        model_name=payload.get("model_name"),
        model_version=payload.get("model_version"),
        harness_version=payload.get("harness_version"),
        system_instruction=payload.get("system_instruction", ""),
        system_instruction_version=payload.get("system_instruction_version", "RUNTIME-PROMPT-v1"),
        original_user_goal=payload.get("original_user_goal", ""),
        task_objectives=list(payload.get("task_objectives") or []),
        acceptance_criteria=list(payload.get("acceptance_criteria") or []),
        immutable_constraints=list(payload.get("immutable_constraints") or []),
        policy_references=list(payload.get("policy_references") or []),
        current_plan=list(payload.get("current_plan") or []),
        completed_steps=list(payload.get("completed_steps") or []),
        pending_steps=list(payload.get("pending_steps") or []),
        current_step=payload.get("current_step", ""),
        current_step_status=payload.get("current_step_status", ""),
        rolling_summary=(summary or {}).get("summary_content"),
        summary_id=(summary or {}).get("summary_id"),
        summary_version=int((summary or {}).get("summary_version", 0)),
        key_facts=list((summary or {}).get("key_facts") or []),
        events=_normalized_events(payload),
        memory=list(payload.get("memory") or []),
        tool_results=list(payload.get("tool_results") or []),
        artifact_references=list(payload.get("artifact_references") or []),
        active_tool_invocations=list(payload.get("active_tool_invocations") or []),
        unresolved_questions=list(payload.get("unresolved_questions") or []),
        event_cursor=int(payload.get("event_cursor", 0)),
        compression_count=int(payload.get("compression_count", 0)),
        budget_config=dict(payload.get("budget_config") or {}),
        context_window_override=int(payload.get("context_window_override", 0)),
        graph_id=payload.get("graph_id"),
        graph_version=int(payload["graph_version"]) if payload.get("graph_version") is not None else None,
        node_id=payload.get("node_id"),
        parent_node_ids=list(payload.get("parent_node_ids") or []),
        node_type=payload.get("node_type"),
        context_policy=payload.get("context_policy"),
        node_context_payload=payload.get("node_context_payload"),
        parent_run_id=payload.get("parent_run_id"),
        delegation_id=payload.get("delegation_id"),
        capability_token_id=payload.get("capability_token_id"),
        context_source=payload.get("context_source", "root"),
        source_context_pack_ids=list(payload.get("source_context_pack_ids") or []),
    )


async def _latest_summary(session, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    summaries = await _persistence.list_summaries(session, tenant_id, run_id, limit=1)
    return summaries[0] if summaries else None


@activity.defn
async def assemble_context_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble and persist a ContextPack; offload large tool results to artifacts."""
    tenant_id = payload["tenant_id"]
    run_id = payload["run_id"]
    _heartbeat({"stage": "context_assemble", "run_id": run_id})
    estimator = get_estimator(payload.get("model_name"))
    async with session_for_tenant(tenant_id) as session:
        summary = payload.get("summary") or await _latest_summary(session, tenant_id, run_id)

        # Offload large tool results via the existing object store; never mutate Run Events.
        offloaded_refs: list[dict[str, Any]] = []
        tool_results = list(payload.get("tool_results") or [])
        processed_results: list[dict[str, Any]] = []
        for result in tool_results:
            raw = result.get("result", result)
            if should_offload(raw):
                ref = await _persistence.put_artifact(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    result=raw if isinstance(raw, dict) else {"value": raw},
                    source_event_id=str(result.get("event_id", "")) or None,
                    acl=list(payload.get("artifact_acl") or []),
                    summary=summarize_result(raw),
                )
                processed_results.append(
                    {
                        "tool_call_id": result.get("tool_call_id"),
                        "event_id": result.get("event_id"),
                        "offloaded": True,
                        "artifact_id": ref.artifact_id,
                        "artifact_ref": ref.object_ref,
                        "summary": ref.summary,
                        "relevance_score": result.get("relevance_score", 0.5),
                    }
                )
                offloaded_refs.append({"artifact_id": ref.artifact_id, "object_ref": ref.object_ref, "summary": ref.summary})
            else:
                processed_results.append(result)

        pending = dict(payload)
        pending["tool_results"] = processed_results
        pending["artifact_references"] = list(payload.get("artifact_references") or []) + offloaded_refs

        attempt_input = _build_assembly_input(pending, summary)
        degraded = False
        reason: str | None = None
        try:
            pack = assemble(attempt_input, estimator, now_iso=_now())
        except ContextOverflowError as exc:
            # Safe degradation: keep pinned goal/constraints, drop low-priority
            # history and memory, then rebuild. Still-overflow raises explicitly.
            reason = str(exc)
            degraded = True
            fallback = _build_assembly_input(pending, summary)
            fallback.events = safe_truncate_events(fallback.events, max_tokens=512, estimator=estimator)
            fallback.memory = []
            fallback.tool_results = []
            fallback.artifact_references = []
            pack = assemble(fallback, estimator, now_iso=_now())

        pack.metadata.degraded = degraded
        pack.metadata.degradation_reason = reason
        pack.metadata.context_pack_id = payload.get("context_pack_id") or str(uuid4())
        await _persistence.save_pack(session, pack)
        await append_event(
            session,
            UUID(run_id),
            tenant_id,
            "CONTEXT_PACK_BUILT",
            {
                "context_pack_id": pack.metadata.context_pack_id,
                "schema_version": pack.schema_version,
                "event_cursor": pack.metadata.event_cursor,
                "degraded": degraded,
                "estimated_tokens": pack.budget.estimated_tokens,
                "tokenizer": pack.budget.tokenizer_name,
            },
        )
        await session.commit()

    model_payload = pack_to_model_payload(
        pack,
        list(payload.get("available_tools") or []),
        list(payload.get("selected_skills") or []),
    )
    return {
        "pack": pack.model_dump(mode="json"),
        "model_payload": model_payload,
        "context_pack_id": pack.metadata.context_pack_id,
        "degraded": degraded,
        "degradation_reason": reason,
        "artifact_refs": list(offloaded_refs),
        "node_id": pack.metadata.node_id,
        "graph_id": pack.metadata.graph_id,
        "parent_run_id": pack.metadata.parent_run_id,
        "delegation_id": pack.metadata.delegation_id,
        "context_source": pack.metadata.context_source,
    }


@activity.defn
async def summarize_context_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the LLM summary step, validate it, and persist idempotently.

    On failure returns ``{"status": "FAILED", ...}`` so the workflow can apply
    the safe-truncation fallback without losing any original events.
    """
    tenant_id = payload["tenant_id"]
    run_id = payload["run_id"]
    _heartbeat({"stage": "context_summary", "run_id": run_id})
    request = SummaryRequest(
        run_id=run_id,
        tenant_id=tenant_id,
        source_event_start=int(payload.get("source_event_start", 0)),
        source_event_end=int(payload.get("source_event_end", 0)),
        source_event_ids=list(payload.get("source_event_ids") or []),
        events=list(payload.get("events") or []),
        previous_summary=payload.get("previous_summary", ""),
        version=int(payload.get("summary_version", 1)),
        model_version=payload.get("model_version", ""),
    )
    try:
        summary_input = build_summary_input(request)
        result = await model_adapter.complete_json(summary_input["system"], summary_input["user"])
        validated = validate_summary(result, request)
        async with session_for_tenant(tenant_id) as session:
            persisted = await _persistence.save_summary(session, request, validated["summary"], validated["key_facts"])
            await append_event(
                session,
                UUID(run_id),
                tenant_id,
                "CONTEXT_SUMMARY_STORED",
                {
                    "summary_id": persisted["summary_id"],
                    "summary_version": persisted["summary_version"],
                    "source_event_end": request.source_event_end,
                    "content_hash": persisted["content_hash"],
                    "idempotent_hit": persisted["idempotent_hit"],
                },
            )
            await session.commit()
        return {"status": "STORED", **persisted}
    except Exception as exc:
        return {"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


@activity.defn
async def load_context_state_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Load persisted context state after worker restart (summary + last pack)."""
    tenant_id = payload["tenant_id"]
    run_id = payload["run_id"]
    async with session_for_tenant(tenant_id) as session:
        summary = await _latest_summary(session, tenant_id, run_id)
        pack = await _persistence.load_pack(session, tenant_id, run_id)
    return {
        "summary": summary,
        "last_context_pack_id": pack.metadata.context_pack_id if pack else None,
        "last_event_cursor": pack.metadata.event_cursor if pack else 0,
    }
