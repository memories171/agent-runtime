from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_runtime.checkpoint.models import (
    CHECKPOINT_SCHEMA_VERSION,
    AgentStateSnapshot,
    BudgetState,
    ContextState,
    ExecutionState,
    PlanState,
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "credential", "authorization", "api_key")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _scrub(value: Any) -> Any:
    """移除原始 token / secret，仅保留授权判定所需的非敏感字段。"""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if not _is_secret_key(k)}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def build_state_snapshot(state: dict[str, Any]) -> AgentStateSnapshot:
    history = list(state.get("history", []))
    tool_calls = list(state.get("tool_calls", []))
    llm_calls = list(state.get("llm_calls", []))
    principal = _scrub(state.get("principal", {}))

    current_step = str(state.get("current_step", "") or "")
    last_completed_step = state.get("last_completed_step")
    recent_tool_results = [
        item for item in history if item.get("type") == "tool_result"
    ][-16:]

    plan_state = PlanState(
        objective=state.get("prompt", ""),
        acceptance_criteria=list(state.get("acceptance_criteria", [])),
        plan=list(state.get("plan", [])),
        completed_steps=list(state.get("completed_steps", [])),
        pending_steps=list(state.get("pending_steps", [])),
        current_node=dict(state.get("current_node", {})),
        dependencies=dict(state.get("dependencies", {})),
    )
    context_state = ContextState(
        system_instruction_version=state.get("system_instruction_version", "RUNTIME-PROMPT-v1"),
        user_task=state.get("prompt", ""),
        history_summary=state.get("context_summary", ""),
        recent_messages=history[-16:],
        history_ref=state.get("context_summary_ref"),
        history_event_range={
            "history_length": len(history),
            "message_history_ref": state.get("message_history_ref"),
        },
        memory_references=list(state.get("memory_context", [])),
        skill_version_references=list(state.get("skills", [])),
        tool_result_references=[
            {"tool": item.get("tool"), "result_ref": f"history[{index}]"}
            for index, item in enumerate(history) if item.get("type") == "tool_result"
        ][-16:],
        context_pack_ref=state.get("context_pack_ref"),
        context_pack_schema_version=state.get("context_pack_schema_version"),
        context_event_cursor=int(state.get("context_event_cursor", 0) or 0),
        context_summary_ref=state.get("context_summary_ref"),
        context_provenance=list(state.get("context_provenance") or []),
    )
    execution_state = ExecutionState(
        current_step=current_step,
        current_step_state=dict(state.get("current_step_state", {})),
        last_completed_step=last_completed_step,
        executed_tools=[item.get("tool") or item.get("name") for item in tool_calls if isinstance(item, dict)],
        confirmed_tool_results=recent_tool_results,
        unconfirmed_tool_calls=list(state.get("unconfirmed_tool_calls", [])),
        retry_count=int(state.get("retry_count", 0) or 0),
        activity_progress=dict(state.get("activity_progress", {})),
        run_status=state.get("status") or state.get("run_status") or "RUNNING",
    )
    budget_state = BudgetState(
        max_duration_seconds=int(state.get("max_duration_seconds", 0) or 0),
        used_duration_seconds=int(state.get("used_duration_seconds", 0) or 0),
        max_tokens=int(state.get("max_tokens", 0) or 0),
        used_tokens=int(state.get("token_input", 0) or 0) + int(state.get("token_output", 0) or 0),
        max_cost_usd=float(state.get("max_cost_usd", 0.0) or 0.0),
        used_cost_usd=float(state.get("estimated_cost_usd", 0.0) or 0.0),
        remaining_steps=int(state.get("remaining_steps", 0) or 0),
    )
    return AgentStateSnapshot(
        plan_state=plan_state,
        context_state=context_state,
        execution_state=execution_state,
        budget_state=budget_state,
        history=history,
        tool_calls=tool_calls,
        llm_calls=llm_calls,
        tool_intents=list(state.get("tool_intents", [])),
        approval_bindings=list(state.get("approval_bindings", [])),
        principal=principal,
    )


def build_envelope_fields(
    checkpoint_id: str,
    tenant_id: str,
    run_id: str,
    step: int,
    state: dict[str, Any],
    snapshot: AgentStateSnapshot,
    *,
    temporal_workflow_id: str = "",
    temporal_execution_id: str = "",
    checkpoint_sequence: int | None = None,
    schema_version: str = CHECKPOINT_SCHEMA_VERSION,
) -> dict[str, Any]:
    token_input = int(state.get("token_input", 0) or 0)
    token_output = int(state.get("token_output", 0) or 0)
    return {
        "checkpoint_id": checkpoint_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "temporal_workflow_id": temporal_workflow_id,
        "temporal_execution_id": temporal_execution_id,
        "checkpoint_sequence": step if checkpoint_sequence is None else checkpoint_sequence,
        "current_step_id": snapshot.execution_state.current_step or f"step-{step}",
        "created_at": datetime.now(timezone.utc),
        "last_completed_step": snapshot.execution_state.last_completed_step,
        "last_application_event_sequence": int(state.get("application_event_sequence", 0) or 0),
        "task_graph_version": str(state.get("task_graph_version", "v1")),
        "graph_state_ref": state.get("graph_state_ref"),
        "current_plan": snapshot.plan_state.plan,
        "completed_steps": snapshot.plan_state.completed_steps,
        "pending_steps": snapshot.plan_state.pending_steps,
        "current_step_state": snapshot.execution_state.current_step_state,
        "context_summary_ref": snapshot.context_state.context_summary_ref or snapshot.context_state.history_ref,
        "message_history_ref": snapshot.context_state.history_event_range.get("message_history_ref"),
        "context_pack_ref": snapshot.context_state.context_pack_ref,
        "context_pack_schema_version": snapshot.context_state.context_pack_schema_version,
        "context_event_cursor": snapshot.context_state.context_event_cursor,
        "tool_intents": snapshot.tool_intents,
        "approval_bindings": snapshot.approval_bindings,
        "memory_references": snapshot.context_state.memory_references,
        "model_version": state.get("model_name"),
        "harness_version": state.get("harness_version"),
        "token_usage": {"input": token_input, "output": token_output, "total": token_input + token_output},
        "cost_usage": {"estimated_usd": float(state.get("estimated_cost_usd", 0.0) or 0.0)},
        "execution_budget": snapshot.budget_state.model_dump(),
        "schema_version": schema_version,
    }
