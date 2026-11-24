from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CHECKPOINT_SCHEMA_VERSION = "2.0"
LEGACY_CHECKPOINT_SCHEMA_VERSION = "1.0"

COMPRESSION_ALGORITHMS = ("zlib",)
ENCRYPTION_ALGORITHMS = ("AES-256-GCM",)

CheckpointPersistenceStatus = Literal[
    "CREATED",
    "SERIALIZING",
    "UPLOADING",
    "VERIFYING",
    "PERSISTED",
    "AVAILABLE_FOR_RECOVERY",
    "CHECKPOINT_FAILED",
    "RETRY",
    "ORPHANED",
]


class PlanState(BaseModel):
    objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    completed_steps: list[dict[str, Any]] = Field(default_factory=list)
    pending_steps: list[dict[str, Any]] = Field(default_factory=list)
    current_node: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)


class ContextState(BaseModel):
    system_instruction_version: str = ""
    user_task: str = ""
    history_summary: str = ""
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    history_ref: str | None = None
    history_event_range: dict[str, Any] = Field(default_factory=dict)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    skill_version_references: list[dict[str, Any]] = Field(default_factory=list)
    tool_result_references: list[dict[str, Any]] = Field(default_factory=list)
    # Context Manager (forward-compatible v2 additions; safe defaults).
    context_pack_ref: str | None = None
    context_pack_schema_version: str | None = None
    context_event_cursor: int = 0
    context_summary_ref: str | None = None
    context_provenance: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionState(BaseModel):
    current_step: str = ""
    current_step_state: dict[str, Any] = Field(default_factory=dict)
    last_completed_step: str | None = None
    executed_tools: list[Any] = Field(default_factory=list)
    confirmed_tool_results: list[dict[str, Any]] = Field(default_factory=list)
    unconfirmed_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    retry_count: int = 0
    activity_progress: dict[str, Any] = Field(default_factory=dict)
    run_status: str = ""


class BudgetState(BaseModel):
    max_duration_seconds: int = 0
    used_duration_seconds: int = 0
    max_tokens: int = 0
    used_tokens: int = 0
    max_cost_usd: float = 0.0
    used_cost_usd: float = 0.0
    remaining_steps: int = 0


class AgentStateSnapshot(BaseModel):
    """完整业务状态快照。序列化后写入对象存储，不进入 PostgreSQL JSONB。"""

    plan_state: PlanState = Field(default_factory=PlanState)
    context_state: ContextState = Field(default_factory=ContextState)
    execution_state: ExecutionState = Field(default_factory=ExecutionState)
    budget_state: BudgetState = Field(default_factory=BudgetState)
    history: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_intents: list[dict[str, Any]] = Field(default_factory=list)
    approval_bindings: list[dict[str, Any]] = Field(default_factory=list)
    principal: dict[str, Any] = Field(default_factory=dict)


class SnapshotPayload(BaseModel):
    """对象存储中的外层载荷：状态快照（state_digest 存于 Envelope，不进入被签名内容）。"""

    schema_version: str = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: str
    state: AgentStateSnapshot


class CheckpointEnvelope(BaseModel):
    """版本化 Checkpoint 元数据。PostgreSQL 仅保存此结构，不保存完整 state。"""

    schema_version: str = CHECKPOINT_SCHEMA_VERSION
    checkpoint_id: str
    tenant_id: str
    run_id: str
    temporal_workflow_id: str = ""
    temporal_execution_id: str = ""
    checkpoint_sequence: int
    current_step_id: str = ""
    workflow_version: str = "v1"
    task_graph_version: str = "v1"
    # Forward-compatible graph state reference (optional; v2 readers ignore it).
    graph_state_ref: dict[str, Any] | None = None
    created_at: datetime
    state_digest: str
    blob_sha256: str
    blob_size_bytes: int
    state_blob_ref: str
    compression_algorithm: Literal["zlib"] = "zlib"
    encryption_algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    encryption_key_id: str = ""
    last_completed_step: str | None = None
    last_application_event_sequence: int = 0
    current_plan: list[dict[str, Any]] = Field(default_factory=list)
    completed_steps: list[dict[str, Any]] = Field(default_factory=list)
    pending_steps: list[dict[str, Any]] = Field(default_factory=list)
    current_step_state: dict[str, Any] = Field(default_factory=dict)
    context_summary_ref: str | None = None
    message_history_ref: str | None = None
    context_pack_ref: str | None = None
    context_pack_schema_version: str | None = None
    context_event_cursor: int = 0
    tool_intents: list[dict[str, Any]] = Field(default_factory=list)
    approval_bindings: list[dict[str, Any]] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    model_version: str | None = None
    harness_version: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    cost_usage: dict[str, Any] = Field(default_factory=dict)
    execution_budget: dict[str, Any] = Field(default_factory=dict)
    persistence_status: CheckpointPersistenceStatus = "CREATED"
