"""Task Graph Engine 核心模型（纯定义，无 IO）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

GraphStatus = Literal[
    "PLANNING", "READY", "RUNNING", "WAITING", "PAUSED",
    "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT",
]

NodeStatus = Literal[
    "PENDING", "READY", "RUNNING", "WAITING_APPROVAL", "WAITING_INPUT",
    "RETRY_WAIT", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "SKIPPED",
]

DependencyType = Literal["SUCCESS", "COMPLETION", "FAILURE"]
FailurePolicy = Literal["FAIL_FAST", "CONTINUE_INDEPENDENT", "MANUAL_INTERVENTION"]
NodeType = Literal["task", "analysis", "aggregation", "decision", "sub_agent"]
ContextPolicy = Literal["summary_only", "full", "refs_only"]
GraphEventType = Literal[
    "GRAPH_CREATED", "GRAPH_VALIDATED", "GRAPH_STARTED",
    "NODE_READY", "NODE_STARTED", "NODE_COMPLETED", "NODE_FAILED",
    "NODE_RETRY", "NODE_SKIPPED", "NODE_CANCELLED", "GRAPH_PAUSED", "GRAPH_RESUMED",
    "GRAPH_COMPLETED", "GRAPH_FAILED", "NODE_WAITING_APPROVAL",
    "NODE_WAITING_INPUT", "GRAPH_CANCELLED", "GRAPH_REPLANNED",
    "NODE_TIMED_OUT", "GRAPH_TIMED_OUT",
    "AGENT_DELEGATED", "AGENT_STARTED", "AGENT_COMPLETED", "AGENT_FAILED",
    "AGENT_CANCELLED", "AGENT_TIMED_OUT", "AGENT_REVOKED", "AGENT_CANCEL_REQUESTED",
    "PARENT_TIMEOUT", "CHILD_TIMEOUT", "CHILD_CANCEL_PROPAGATED", "CHILD_CANCEL_UNCONFIRMED",
    "CAPABILITY_ISSUED", "CAPABILITY_DENIED",
    "CHILD_CONTEXT_CREATED", "CONTEXT_PROJECTED", "CONTEXT_REFERENCE_REJECTED",
]

RETRYABLE_ERRORS = ("timeout", "network", "rate_limit", "temporary", "unavailable")
NON_RETRYABLE_ERRORS = (
    "permission_denied", "approval_denied", "validation_error",
    "policy_denied", "schema_error", "invalid_argument",
    "capability_denied", "token_revoked", "token_expired",
)


class GraphLimits(BaseModel):
    max_nodes: int = 30
    max_graph_depth: int = 10
    max_concurrency: int = 5
    max_node_steps: int = 8
    allowed_high_risk_nodes: set[str] = Field(default_factory=set)
    allowed_agent_roles: set[str] = Field(default_factory=set)


class RetryPolicyConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_interval_seconds: float = Field(default=2.0, ge=0.0)
    backoff_coefficient: float = Field(default=2.0, ge=1.0)
    maximum_interval_seconds: float = Field(default=30.0, ge=1.0)
    retryable_errors: list[str] = Field(default_factory=lambda: list(RETRYABLE_ERRORS))
    non_retryable_errors: list[str] = Field(default_factory=lambda: list(NON_RETRYABLE_ERRORS))


class GraphNode(BaseModel):
    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")
    node_type: NodeType = "task"
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    retry_policy: RetryPolicyConfig = Field(default_factory=RetryPolicyConfig)
    timeout_seconds: int = Field(default=300, ge=1, le=7200)
    required_permissions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    context_policy: ContextPolicy = "summary_only"
    execution_order: int = 0
    agent_role: str | None = None
    context_references: list[dict[str, Any]] = Field(default_factory=list)


class GraphEdge(BaseModel):
    edge_id: str = Field(default="", max_length=128)
    graph_id: str = Field(default="", max_length=128)
    source_node_id: str = Field(min_length=1, max_length=128)
    target_node_id: str = Field(min_length=1, max_length=128)
    dependency_type: DependencyType = "SUCCESS"
    # 动态条件分支接口预留：第一阶段只接受 None，拒绝非空条件以免产生未定义语义。
    condition: dict[str, Any] | None = None


class GraphProposal(BaseModel):
    """LLM 生成的提案，验证通过后才能成为 GraphDefinition。"""
    root_goal: str = Field(default="", max_length=20_000)
    acceptance_criteria: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=5, ge=1, le=20)
    failure_policy: FailurePolicy = "CONTINUE_INDEPENDENT"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    model_notes: str = Field(default="", max_length=2_000)


class GraphDefinition(BaseModel):
    graph_id: str
    run_id: str
    tenant_id: str
    graph_version: int = 1
    graph_status: GraphStatus = "PLANNING"
    root_goal: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    max_concurrency: int = 5
    failure_policy: FailurePolicy = "CONTINUE_INDEPENDENT"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def node_map(self) -> dict[str, GraphNode]:
        return {node.node_id: node for node in self.nodes}


class ValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    max_depth: int = 0


class NodeResult(BaseModel):
    node_id: str
    graph_id: str
    tenant_id: str = ""
    run_id: str = ""
    status: NodeStatus = "COMPLETED"
    output: dict[str, Any] | None = None
    error: str | None = None
    error_class: str | None = None
    attempts: int = 0
    token_input: int = 0
    token_output: int = 0
    duration_ms: int = 0
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_pack_id: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None


class GraphEvent(BaseModel):
    graph_id: str
    run_id: str
    tenant_id: str
    event_type: GraphEventType
    sequence_no: int = 0
    node_id: str | None = None
    parent_node_id: str | None = None
    execution_duration_ms: int = 0
    token_usage: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


def terminal(status: NodeStatus) -> bool:
    return status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "SKIPPED"}
