from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent_runtime.config import settings


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_runs_tenant_kind", "tenant_id", "run_kind", "created_at"),
    )

    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    run_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="ROOT")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pause_state: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_created", "run_id", "created_at"),)

    event_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parent_run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    delegation_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowOutbox(Base):
    __tablename__ = "workflow_outbox"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_outbox_command"),
        Index("ix_outbox_dispatch", "status", "next_attempt_at"),
        Index("ix_outbox_command_type", "command_type", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    command_id: Mapped[str] = mapped_column(String(256), nullable=False)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False, default="START_WORKFLOW")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_tool_idempotency"),)

    invocation_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    canonical_arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    result_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    reconciliation_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalBinding(Base):
    __tablename__ = "approval_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "nonce", name="uq_approval_nonce"),
        UniqueConstraint("tenant_id", "run_id", "tool_call_id", "action_hash", name="uq_approval_action"),
    )

    approval_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    target_resource_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    operation_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    canonical_arguments: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    canonical_arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="HIGH")
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    signature_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunTelemetryRecord(Base):
    __tablename__ = "run_telemetry"
    __table_args__ = (UniqueConstraint("run_id", name="uq_run_telemetry_run"),)

    telemetry_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    harness_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step: Mapped[str] = mapped_column(String(256), nullable=False, default="初始化")
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tool_calls_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    llm_calls_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CheckpointRecord(Base):
    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_checkpoint_idempotency"),
        UniqueConstraint("run_id", "checkpoint_sequence", name="uq_checkpoint_run_sequence"),
    )

    checkpoint_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    size_kb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SAVED")
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    duplicate_protection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checkpoint_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="2.0")
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    blob_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    blob_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_blob_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compression_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="zlib")
    encryption_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="AES-256-GCM")
    encryption_key_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    envelope_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    persistence_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")


class TaskGraphRecord(Base):
    __tablename__ = "task_graphs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "graph_version", name="uq_graph_run_version"),
        Index("ix_graph_tenant_run", "tenant_id", "run_id"),
        Index("ix_graph_tenant_status", "tenant_id", "graph_status"),
    )

    graph_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    graph_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNING")
    root_goal: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    failure_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="CONTINUE_INDEPENDENT")
    planner_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    definition_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskGraphVersionRecord(Base):
    __tablename__ = "task_graph_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_id", "graph_version", name="uq_task_graph_version"),
        Index("ix_task_graph_version_lookup", "tenant_id", "graph_id", "graph_version"),
    )

    version_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_id: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    planner_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskGraphNodeRecord(Base):
    __tablename__ = "task_graph_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_id", "node_key", name="uq_graph_node_key"),
        Index("ix_graph_node_status", "tenant_id", "graph_id", "status"),
    )

    node_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    graph_id: Mapped[str] = mapped_column(String(128), ForeignKey("task_graphs.graph_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, default="task")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dependencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    retry_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    required_permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="summary_only")
    execution_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    token_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifact_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_pack_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_event_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskGraphEdgeRecord(Base):
    __tablename__ = "task_graph_edges"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_id", "edge_key", name="uq_graph_edge_key"),
    )

    edge_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    graph_id: Mapped[str] = mapped_column(String(128), ForeignKey("task_graphs.graph_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    edge_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GraphEventRecord(Base):
    __tablename__ = "graph_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "graph_id", "sequence_no", name="uq_graph_event_sequence"),
        Index("ix_graph_event_graph_seq", "tenant_id", "graph_id", "sequence_no"),
        Index("ix_graph_event_run_created", "tenant_id", "run_id", "created_at"),
    )

    event_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    graph_id: Mapped[str] = mapped_column(String(128), ForeignKey("task_graphs.graph_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delegation_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    capability_token_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapabilityTokenRecord(Base):
    __tablename__ = "capability_tokens"
    __table_args__ = (
        UniqueConstraint("tenant_id", "token_id", name="uq_capability_token_tenant_token"),
        Index("ix_capability_token_subject", "tenant_id", "subject_run_id"),
        Index("ix_capability_token_parent", "tenant_id", "parent_token_id"),
    )

    token_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_token_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    subject_run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    subject_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resource_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentDelegationRecord(Base):
    __tablename__ = "agent_delegations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "delegation_key", name="uq_agent_delegation_key"),
        UniqueConstraint("tenant_id", "parent_run_id", "node_id", "attempt", name="uq_agent_delegation_node_attempt"),
        Index("uq_agent_delegation_child_workflow", "tenant_id", "child_workflow_id", unique=True),
        Index("ix_agent_delegation_parent", "tenant_id", "parent_run_id", "status"),
        Index("ix_agent_delegation_child", "tenant_id", "child_run_id"),
        Index("ix_agent_delegation_graph_node", "tenant_id", "graph_id", "node_id"),
        Index("ix_agent_delegation_root", "tenant_id", "root_run_id"),
        Index("ix_agent_delegation_updated", "tenant_id", "updated_at"),
    )

    delegation_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    child_run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    root_run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    parent_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    child_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_token_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    delegation_key: Mapped[str] = mapped_column(String(256), nullable=False)
    child_workflow_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ISSUED")
    graph_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerFailureRecord(Base):
    __tablename__ = "worker_failures"

    failure_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECOVERING")
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovered_from_checkpoint_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    recovery_position: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    duplicate_protection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    recovery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activity_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SkillState(Base):
    __tablename__ = "skill_states"

    skill_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BenchmarkExperimentRecord(Base):
    """Model × Harness 矩阵评测结果（真实 runner 落库）。"""
    __tablename__ = "benchmark_experiments"
    __table_args__ = (Index("ix_benchmark_tenant_created", "tenant_id", "created_at"),)

    experiment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    batch: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    harness: Mapped[str] = mapped_column(String(128), nullable=False)
    harness_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dataset: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    task_completion_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recovery_success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tool_call_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unauthorized_access_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prompt_injection_success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p95_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    token_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_per_success_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)


class AblationRecordTable(Base):
    """Benchmark 消融实验结果。"""
    __tablename__ = "benchmark_ablations"
    __table_args__ = (Index("ix_ablation_tenant_created", "tenant_id", "created_at"),)

    ablation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    experiment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    removed_component: Mapped[str] = mapped_column(String(128), nullable=False)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    delta_pp: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recovery_success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unauthorized_access_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeDocumentRecord(Base):
    """知识库文档：解析清洗后入库，含统计信息。"""
    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_kb_doc_tenant_created", "tenant_id", "created_at"),)

    document_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="markdown")
    raw_content: Mapped[str | None] = mapped_column(Text(), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunkRecord(Base):
    """知识库切分块：含向量（JSONB）、标题和原文，用于溯源。"""
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_kb_chunk_tenant_doc", "tenant_id", "document_id"),
        Index("ix_kb_chunk_tenant_created", "tenant_id", "created_at"),
    )

    chunk_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    vector: Mapped[list] = mapped_column(JSON, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationRecord(Base):
    """对话式入口的会话（与 Agent Run 解耦，可关联 session_id）。"""
    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conv_tenant_updated", "tenant_id", "updated_at"),)

    conversation_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="新对话")
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_msg_conv_created", "conversation_id", "created_at"),)

    message_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("chat_conversations.conversation_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryRecord(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (Index("ix_memory_tenant_scope", "tenant_id", "scope", "owner_id"),)

    memory_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContextPackRecord(Base):
    __tablename__ = "context_packs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "context_pack_id", name="uq_context_pack_id"),
        Index("ix_context_pack_run_created", "tenant_id", "run_id", "created_at"),
    )

    pack_row_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    context_pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pack_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    compression_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compression_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    degradation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContextSummaryRecord(Base):
    __tablename__ = "context_summaries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_context_summary_idem"),
        Index("ix_context_summary_run_version", "tenant_id", "run_id", "summary_version"),
    )

    summary_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary_version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_content: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_facts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContextArtifactRecord(Base):
    __tablename__ = "context_artifacts"
    __table_args__ = (Index("ix_context_artifact_run", "tenant_id", "run_id", "created_at"),)

    artifact_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acl: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinancialCustomer(Base):
    __tablename__ = "financial_customers"
    __table_args__ = (Index("ix_fin_customer_tenant", "tenant_id", "customer_id", unique=True),)

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    customer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    risk_rating: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    kyc_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinancialAccount(Base):
    __tablename__ = "financial_accounts"
    __table_args__ = (Index("ix_fin_account_tenant", "tenant_id", "account_id", unique=True), Index("ix_fin_account_customer", "tenant_id", "customer_id"))

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(64), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FinancialTransaction(Base):
    __tablename__ = "financial_transactions"
    __table_args__ = (
        Index("ix_fin_tx_tenant_account_time", "tenant_id", "account_id", "occurred_at"),
        Index("ix_fin_tx_tenant_reference", "tenant_id", "reference"),
    )

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    counterparty_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    counterparty_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FinancialReviewCase(Base):
    __tablename__ = "financial_review_cases"
    __table_args__ = (
        Index("ix_fin_case_tenant_status", "tenant_id", "status", "created_at"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_fin_case_tenant_idempotency"),
    )

    case_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_transaction_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")


class FinancialResourceGrant(Base):
    __tablename__ = "financial_resource_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "resource_type", "resource_id", "permission", name="uq_fin_resource_grant"),
        Index("ix_fin_grant_lookup", "tenant_id", "user_id", "resource_type", "resource_id"),
    )

    grant_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinancialAuditLog(Base):
    __tablename__ = "financial_audit_logs"
    __table_args__ = (Index("ix_fin_audit_tenant_created", "tenant_id", "created_at"),)

    audit_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_org_tenant_name"),)

    organization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        Index("ix_membership_user_status", "user_id", "status"),
    )

    membership_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    role_assignments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base):
    __tablename__ = "roles"

    role_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Permission(Base):
    __tablename__ = "permissions"

    permission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(String(128), ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(String(128), ForeignKey("permissions.permission_id", ondelete="CASCADE"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "role_id", name="uq_user_role_tenant"),)

    user_role_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[str] = mapped_column(String(128), ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceAcl(Base):
    __tablename__ = "resource_acls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "resource_type", "resource_id", "permission", name="uq_resource_acl"),
        Index("ix_resource_acl_lookup", "tenant_id", "user_id", "resource_type", "resource_id"),
    )

    acl_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_logs"
    __table_args__ = (Index("ix_auth_audit_tenant_created", "tenant_id", "created_at"),)

    audit_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_for_tenant(tenant_id: str | None):
    from agent_runtime.rls import tenant_session

    async with tenant_session(SessionLocal, tenant_id) as session:
        yield session


async def get_session():
    from agent_runtime.rls import current_tenant_id, set_tenant_context

    async with SessionLocal() as session:
        await set_tenant_context(session, current_tenant_id.get())
        try:
            yield session
        finally:
            await set_tenant_context(session, None)


async def append_event(
    session: AsyncSession, run_id, tenant_id: str, event_type: str, payload: dict | None = None
) -> None:
    session.add(
        RunEvent(
            run_id=run_id,
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload or {},
        )
    )


async def fetch_run(session: AsyncSession, run_id, tenant_id: str) -> RunRecord | None:
    result = await session.execute(
        select(RunRecord).where(RunRecord.run_id == run_id, RunRecord.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
