"""B7 Checkpoint Restore — versioned restore contract and state injection.

Design (minimum complete restore):
  * A restore always starts a NEW Run (new run_id, new Temporal workflow).
    It never mutates the original Run's Temporal history — this keeps the
    restored state consistent with its own history from the first event.
  * The snapshot's business state is injected deterministically at the top of
    the new workflow, guarded by ``workflow.patched`` so old histories replay.
  * Side-effect idempotency: ``confirmed_tool_results`` recorded before the
    checkpoint are carried into the contract. The restored workflow marks
    those tool_call_ids as already-confirmed and does NOT re-execute their
    external side effects; it reuses the recorded observation instead.
  * tenant/ACL/capability consistency is validated at the API boundary before
    any workflow is started.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.checkpoint.models import AgentStateSnapshot, CheckpointEnvelope

RESTORE_CONTRACT_VERSION = "1.0"


class RestoreSource(BaseModel):
    source_run_id: str
    checkpoint_id: str
    checkpoint_sequence: int
    tenant_id: str
    state_digest: str


class RestoreContract(BaseModel):
    """Frozen, versioned payload embedded in the restored WorkflowInput."""

    version: str = RESTORE_CONTRACT_VERSION
    source: RestoreSource
    state: AgentStateSnapshot
    # idempotency_key -> recorded tool_result observation from history.
    # These tool calls MUST NOT produce a second external side effect.
    confirmed_tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # capability/ACL snapshot frozen at restore time; enforced by activities.
    capability_token_id: str | None = None
    restored_by_user_id: str = ""


def build_restore_contract(
    *,
    envelope: CheckpointEnvelope,
    state: AgentStateSnapshot,
    source_run_id: str,
    restored_by_user_id: str,
    capability_token_id: str | None = None,
) -> RestoreContract:
    """Build a contract from a verified checkpoint snapshot.

    Confirmed tool results are the ``tool_result`` observations present in the
    snapshot history. They are keyed by a stable per-call key (tool name +
    call_id when present, otherwise the observation index) so the workflow can
    reuse them without re-running the tool.
    """
    confirmed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(state.history):
        if not isinstance(item, dict) or item.get("type") != "tool_result":
            continue
        if "result" not in item and "error" not in item:
            continue  # CONFIRMED-with-no-result edge case: never reused.
        tool = str(item.get("tool") or "unknown")
        call_id = str(item.get("call_id") or item.get("tool_call_id") or f"idx-{index}")
        confirmed[f"{tool}:{call_id}"] = item

    return RestoreContract(
        version=RESTORE_CONTRACT_VERSION,
        source=RestoreSource(
            source_run_id=source_run_id,
            checkpoint_id=envelope.checkpoint_id,
            checkpoint_sequence=envelope.checkpoint_sequence,
            tenant_id=envelope.tenant_id,
            state_digest=envelope.state_digest,
        ),
        state=state,
        confirmed_tool_results=confirmed,
        capability_token_id=capability_token_id,
        restored_by_user_id=restored_by_user_id,
    )


def inject_restore_state(state: dict[str, Any], contract: RestoreContract) -> dict[str, Any]:
    """Inject snapshot business state into a fresh workflow state dict.

    Only business-level fields are restored; workflow-internal control fields
    (pause/cancel signals, child registry, shutdown reason) are intentionally
    NOT restored — they are re-created by the new workflow's own lifecycle.
    """
    snap = contract.state
    for key in ("history", "tool_calls", "llm_calls", "tool_intents", "approval_bindings"):
        source = getattr(snap, key, None)
        if source is not None:
            state[key] = [dict(item) if isinstance(item, dict) else item for item in source]

    plan = snap.plan_state
    for src_key, dst_key in (
        ("objective", "prompt"),
        ("acceptance_criteria", "acceptance_criteria"),
        ("plan", "plan"),
        ("completed_steps", "completed_steps"),
        ("pending_steps", "pending_steps"),
        ("current_node", "current_node"),
    ):
        value = getattr(plan, src_key, None)
        if value is not None:
            state[dst_key] = dict(value) if isinstance(value, dict) else (
                [dict(v) if isinstance(v, dict) else v for v in value] if isinstance(value, list) else value
            )

    ctx = snap.context_state
    state["context_summary"] = ctx.history_summary
    state["context_event_cursor"] = ctx.context_event_cursor
    state["context_provenance"] = list(ctx.context_provenance)

    exe = snap.execution_state
    state["current_step"] = exe.current_step
    state["last_completed_step"] = exe.last_completed_step
    state["current_step_state"] = dict(exe.current_step_state)
    state["unconfirmed_tool_calls"] = list(exe.unconfirmed_tool_calls)
    state["status"] = exe.run_status

    budget = snap.budget_state
    state["max_duration_seconds"] = budget.max_duration_seconds
    state["used_duration_seconds"] = budget.used_duration_seconds
    state["max_tokens"] = budget.max_tokens
    state["token_input"] = budget.used_tokens
    state["remaining_steps"] = budget.remaining_steps

    # principal: keep the NEW run's principal (caller identity), but carry
    # organization/roles/permissions from the snapshot for capability parity.
    if snap.principal:
        for key in ("organization_id", "roles", "permissions"):
            if key in snap.principal and snap.principal[key] is not None:
                state["principal"][key] = snap.principal[key]

    state["restore_contract"] = contract.model_dump(mode="json")
    return state
