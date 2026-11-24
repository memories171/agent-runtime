"""Versioned ContextPack data structures.

Every externally-sourced item carries a :class:`Provenance` record so the
origin, trust level and relevance can be inspected and enforced downstream.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

CONTEXT_PACK_SCHEMA_VERSION = "1.0"

TrustLevel = Literal["system", "verified", "derived", "untrusted"]
Sensitivity = Literal["public", "internal", "confidential", "restricted"]
SourceType = Literal[
    "system_instruction",
    "policy",
    "user_goal",
    "plan",
    "execution_state",
    "run_event",
    "summary",
    "memory",
    "tool_result",
    "artifact",
    "model_inference",
]


class Provenance(BaseModel):
    """Traceability for a single context item."""

    source_type: SourceType
    source_id: str = ""
    source_event_id: str | None = None
    created_at: str | None = None
    trust_level: TrustLevel = "untrusted"
    sensitivity: Sensitivity = "internal"
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    tenant_id: str | None = None

    @classmethod
    def system(cls) -> "Provenance":
        return cls(source_type="system_instruction", trust_level="system", sensitivity="internal")

    @classmethod
    def untrusted(cls, source_type: SourceType, source_id: str = "", **kwargs: Any) -> "Provenance":
        return cls(source_type=source_type, source_id=source_id, trust_level="untrusted", **kwargs)


class ContextItem(BaseModel):
    """A context value plus its provenance and token cost."""

    value: Any
    provenance: Provenance
    estimated_tokens: int = 0

    def redacted(self) -> "ContextItem":
        """Return a copy with sensitive values masked for display/telemetry."""
        return self.model_copy(update={"value": _redact_value(self.value)})


def _redact_value(value: Any) -> Any:
    secret_markers = ("token", "secret", "password", "credential", "authorization", "api_key", "apikey")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in secret_markers):
                out[key] = "***REDACTED***"
            else:
                out[key] = _redact_value(item)
        return out
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        masked = value
        for marker in secret_markers:
            if marker in value.lower():
                return "***REDACTED***"
        # Mask long alphanumeric identifiers that look like keys/tokens.
        if len(masked) > 24 and masked.replace("-", "").replace("_", "").isalnum():
            return masked[:4] + "***" + masked[-4:]
        return masked
    return value


class ContextMetadata(BaseModel):
    context_pack_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = CONTEXT_PACK_SCHEMA_VERSION
    run_id: str
    tenant_id: str
    agent_id: str = "default-agent"
    model_name: str | None = None
    model_version: str | None = None
    harness_version: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_cursor: int = 0
    assembly_latency_ms: int = 0
    compression_count: int = 0
    compression_ratio: float = 0.0
    degraded: bool = False
    degradation_reason: str | None = None
    # Task Graph node scope (additive, backward compatible with v1.0 readers).
    graph_id: str | None = None
    graph_version: int | None = None
    node_id: str | None = None
    parent_node_ids: list[str] = Field(default_factory=list)
    node_type: str | None = None
    context_policy: str | None = None
    # Context ownership / propagation (additive, backward compatible).
    parent_run_id: str | None = None
    delegation_id: str | None = None
    capability_token_id: str | None = None
    context_source: str = "root"
    source_context_pack_ids: list[str] = Field(default_factory=list)


class GoalContext(BaseModel):
    original_user_goal: ContextItem
    task_objectives: list[ContextItem] = Field(default_factory=list)
    acceptance_criteria: list[ContextItem] = Field(default_factory=list)
    immutable_constraints: list[ContextItem] = Field(default_factory=list)
    active_policy_references: list[ContextItem] = Field(default_factory=list)


class ExecutionContext(BaseModel):
    current_plan: list[ContextItem] = Field(default_factory=list)
    completed_steps: list[ContextItem] = Field(default_factory=list)
    pending_steps: list[ContextItem] = Field(default_factory=list)
    current_step: ContextItem | None = None
    current_step_status: str = ""
    active_artifact_references: list[ContextItem] = Field(default_factory=list)
    active_tool_invocations: list[ContextItem] = Field(default_factory=list)
    upstream_node_outputs: list[dict[str, Any]] = Field(default_factory=list)
    upstream_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)


class HistoricalContext(BaseModel):
    rolling_summary: ContextItem | None = None
    summary_id: str | None = None
    summary_version: int = 0
    recent_events: list[ContextItem] = Field(default_factory=list)
    historical_decisions: list[ContextItem] = Field(default_factory=list)
    important_tool_results: list[ContextItem] = Field(default_factory=list)
    unresolved_questions: list[ContextItem] = Field(default_factory=list)


class MemoryContext(BaseModel):
    retrieved_memory_references: list[ContextItem] = Field(default_factory=list)
    memory_source_run_ids: list[str] = Field(default_factory=list)
    memory_confidence: float = 0.0
    memory_scope: str = "user"


class BudgetContext(BaseModel):
    model_context_limit: int = 0
    reserved_output_tokens: int = 0
    system_instruction_tokens: int = 0
    context_tokens: int = 0
    available_tokens: int = 0
    estimated_tokens: int = 0
    safety_margin_tokens: int = 0
    tokenizer_name: str = "heuristic"
    tokens_are_estimate: bool = True
    provider_prompt_tokens: int | None = None
    provider_completion_tokens: int | None = None
    token_delta: int | None = None
    budget_config: dict[str, Any] = Field(default_factory=dict)


class ContextPack(BaseModel):
    """A versioned snapshot of everything the model should see before a call."""

    schema_version: str = CONTEXT_PACK_SCHEMA_VERSION
    metadata: ContextMetadata
    goal: GoalContext
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    historical: HistoricalContext = Field(default_factory=HistoricalContext)
    memory: MemoryContext = Field(default_factory=MemoryContext)
    budget: BudgetContext = Field(default_factory=BudgetContext)

    def display_safe(self) -> dict[str, Any]:
        """Provenance-rich view with secrets masked (for API / inspector)."""
        return {
            "schema_version": self.schema_version,
            "metadata": self.metadata.model_dump(),
            "goal": _dump_items(self.goal),
            "execution": _dump_items(self.execution),
            "historical": _dump_items(self.historical),
            "memory": _dump_items(self.memory),
            "budget": self.budget.model_dump(),
        }


def _dump_items(section: BaseModel) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in section.model_dump().items():
        if isinstance(value, dict) and "provenance" in value and "value" in value:
            item = ContextItem.model_validate(value)
            out[key] = item.redacted().model_dump(mode="json")
        elif isinstance(value, list):
            rendered = []
            for entry in value:
                if isinstance(entry, dict) and "provenance" in entry and "value" in entry:
                    rendered.append(ContextItem.model_validate(entry).redacted().model_dump(mode="json"))
                else:
                    rendered.append(entry)
            out[key] = rendered
        else:
            out[key] = value
    return out
