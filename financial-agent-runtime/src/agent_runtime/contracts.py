from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DataQuality(str, Enum):
    """Unified data-quality state for read-only aggregates.

    OK           complete data, no exclusions or errors.
    DEGRADED     legacy generic degradation state (kept for compatibility).
    PARTIAL      some records excluded but the visible scope is trustworthy.
    MISSING      requested scope cannot be resolved (e.g. root has no edges).
    DENIED       access control excluded data the caller cannot see.
    ERROR        computation/integrity failure (cycle, invalid intervals, ...).
    UNAVAILABLE  aggregate cannot be produced at all (legacy severe state).
    """

    OK = "OK"
    DEGRADED = "DEGRADED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    DENIED = "DENIED"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


RunStatus = Literal[
    "CREATED",
    "QUEUED",
    "RUNNING",
    "PAUSED",
    "WAITING_INPUT",
    "WAITING_APPROVAL",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
]


class Principal(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    scopes: set[str] = Field(default_factory=set)
    organization_id: str | None = None
    roles: set[str] = Field(default_factory=set)
    permissions: set[str] = Field(default_factory=set)
    authentication_method: Literal["dev", "oidc", "system"] = "dev"
    token_expires_at: datetime | None = None
    token_subject: str | None = None
    session_id: str | None = None


class RestoreRequest(BaseModel):
    """B7: start a NEW Run restored from a verified checkpoint of ``run_id``."""

    checkpoint_id: str | None = Field(default=None, min_length=1, max_length=256)
    checkpoint_sequence: int | None = Field(default=None, ge=0)
    agent_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(default="checkpoint_restore", max_length=500)


class RunCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    agent_id: str = "default-agent"
    session_id: str | None = None
    max_steps: int | None = Field(default=None, ge=1, le=200)
    max_duration_seconds: int | None = Field(default=None, ge=60, le=86_400)
    metadata: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list, max_length=10)
    model_name: str | None = Field(default=None, max_length=128)
    graph: dict[str, Any] | None = Field(default=None, description="Task Graph mode config; omitted = linear agent loop")
    orchestration: dict[str, Any] | None = Field(
        default=None,
        description="Sub-Agent orchestration config; omitted = no child agents",
    )


class RunRead(BaseModel):
    run_id: UUID
    tenant_id: str
    user_id: str
    session_id: str
    agent_id: str
    prompt: str
    status: RunStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    model_name: str | None = None
    harness_version: str | None = None
    duration_seconds: float | None = None
    heartbeat_at: str | None = None
    deadline_at: str | None = None
    progress_pct: int | None = None
    current_step: str | None = None
    token_input: int | None = None
    token_output: int | None = None
    estimated_cost_usd: float | None = None
    latest_event: str | None = None


class RunEventRead(BaseModel):
    event_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: str


class UserInput(BaseModel):
    value: str = Field(min_length=1, max_length=20_000)


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=2_000)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    scope: Literal["user", "team", "organization"] = "user"
    memory_type: Literal["working", "episodic", "semantic", "procedural"] = "episodic"
    source_run_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    title: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.8, ge=0, le=1)
    expires_at: str | None = None
    acl: list[str] = Field(default_factory=list)


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=20_000)
    memory_type: Literal["working", "episodic", "semantic", "procedural"] | None = None
    scope: Literal["user", "team", "organization"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    expires_at: str | None = None
    acl: list[str] | None = None


class MemoryRead(BaseModel):
    memory_id: UUID
    scope: str
    memory_type: str
    type: str
    content: str
    title: str
    owner: str
    source_run_id: str | None = None
    confidence: float
    created_at: str
    updated_at: str
    expires_at: str | None = None
    acl: list[str] = Field(default_factory=list)
    archived: bool = False


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None


class AgentDecision(BaseModel):
    action: Literal["tool_call", "ask_user", "request_approval", "finish"]
    message: str | None = None
    tool: ToolCall | None = None
    result: dict[str, Any] | None = None


class AgentTreeNodeRead(BaseModel):
    """A single Agent Tree node. `execution_id` is the external name of run_id."""
    execution_id: UUID
    parent_execution_id: UUID | None = None
    root_execution_id: UUID | None = None
    delegation_id: UUID
    workflow_id: str | None = None
    agent_type: str | None = None
    agent_name: str | None = None
    depth: int = 0
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    has_hidden_children: bool = False


class AgentTreeRead(BaseModel):
    root_execution_id: UUID
    root_visible: bool = True
    nodes: list[AgentTreeNodeRead] = Field(default_factory=list)
    has_hidden_children: bool = False


class AgentTreeAnomaliesRead(BaseModel):
    """Diagnostic view of tree-integrity anomalies.

    Tenant scoped and per-Run ACL filtered: identifiers for hidden or
    unauthorized runs are never included in any field.
    """
    orphans: list[UUID] = Field(default_factory=list)
    cycles: list[list[UUID]] = Field(default_factory=list)
    duplicate_links: list[UUID] = Field(default_factory=list)


class ExecutionMetricsRead(BaseModel):
    """Read-only delegated-agent parallelism metrics.

    ``scope`` is always ``delegated_agents``: the Root Agent has no reliable
    started_at/completed_at interval and is therefore not included.
    ``unavailable_reasons`` contains generic codes only; it never contains node
    identifiers, names, timestamps or hidden-node counts.
    """

    scope: Literal["delegated_agents"] = "delegated_agents"
    current_parallelism: int
    max_parallelism: int
    average_parallelism: float
    total_agent_active_time: float
    delegation_attempt_count: int
    distinct_child_run_count: int
    distinct_node_count: int
    unknown_interval_count: int
    partial_interval_count: int
    data_quality: DataQuality
    unavailable_reasons: list[str] = Field(default_factory=list)


class WorkflowInput(BaseModel):
    run_id: str
    tenant_id: str
    user_id: str
    session_id: str
    agent_id: str
    prompt: str
    scopes: list[str]
    max_steps: int
    max_duration_seconds: int
    skill_ids: list[str] = Field(default_factory=list)
    model_name: str | None = None
    harness_version: str = "dev"
    organization_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    authentication_method: str = "dev"
    token_expires_at: datetime | None = None
    context_config: dict[str, Any] = Field(default_factory=dict)
    graph_config: dict[str, Any] = Field(default_factory=dict)
    # Frozen at run creation so Temporal replay never depends on live settings.
    # None => fall back to settings (backward-compatible with older histories).
    child_cancel_grace_seconds: int | None = None
    run_kind: str = "ROOT"
    parent_run_id: str | None = None
    delegation_id: str | None = None
    capability_token_id: str | None = None
    agent_role: str | None = None
    child_context: dict[str, Any] | None = None
    # B7: immutable, versioned restore contract. When present, the workflow
    # injects the snapshot business state at start (guarded by workflow.patched).
    # Always a plain dict so old histories without the field replay unchanged.
    restore_contract: dict[str, Any] | None = None
