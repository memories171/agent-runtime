"""Sub-Agent Orchestration 核心类型（阶段 1：模型与持久化，无 IO）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

AgentRole = Literal["coordinator", "researcher", "analyst", "reviewer", "executor", "summarizer"]
DEFAULT_AGENT_ROLES: frozenset[str] = frozenset({
    "coordinator", "researcher", "analyst", "reviewer", "executor", "summarizer",
})

DelegationStatus = Literal[
    "ISSUED", "CHILD_STARTING", "RUNNING", "WAITING", "CANCEL_REQUESTED",
    "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED",
]
TERMINAL_DELEGATION_STATUSES: frozenset[str] = frozenset({
    "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED",
})

CapabilityTokenStatus = Literal["ACTIVE", "EXPIRED", "REVOKED"]

RunKind = Literal["ROOT", "SUB_AGENT"]

DELEGATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "ISSUED": frozenset({"CHILD_STARTING", "CANCELLED", "REVOKED"}),
    "CHILD_STARTING": frozenset({"RUNNING", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED"}),
    "RUNNING": frozenset({"WAITING", "CANCEL_REQUESTED", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED"}),
    "WAITING": frozenset({"RUNNING", "CANCEL_REQUESTED", "CANCELLED", "TIMED_OUT", "REVOKED"}),
    # CANCEL_REQUESTED：取消请求已发出，但 Child 尚未确认终止；仍可收敛到任一终态。
    "CANCEL_REQUESTED": frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "TIMED_OUT": frozenset(),
    "REVOKED": frozenset(),
}


class CapabilityGrant(BaseModel):
    """委派请求中的能力声明；实际签发由服务端裁剪，不接受模型自报扩权。"""

    capabilities: set[str] = Field(default_factory=set)
    resource_scope: dict[str, Any] = Field(default_factory=dict)
    max_depth: int = Field(default=1, ge=1, le=8)
    timeout_seconds: int = Field(default=1800, ge=1, le=86_400)


class CapabilityTokenRecord(BaseModel):
    token_id: UUID
    tenant_id: str
    run_id: UUID
    parent_token_id: UUID | None = None
    subject_run_id: UUID | None = None
    subject_agent_id: str | None = None
    capabilities: set[str] = Field(default_factory=set)
    resource_scope: dict[str, Any] = Field(default_factory=dict)
    depth: int = Field(default=0, ge=0)
    max_depth: int = Field(default=3, ge=1)
    expires_at: datetime
    revoked_at: datetime | None = None
    signature_hash: str
    created_at: datetime | None = None

    @property
    def status(self) -> CapabilityTokenStatus:
        if self.revoked_at is not None:
            return "REVOKED"
        now = datetime.now(self.expires_at.tzinfo) if self.expires_at.tzinfo else datetime.now()
        if self.expires_at <= now:
            return "EXPIRED"
        return "ACTIVE"


class AgentDelegationRecord(BaseModel):
    delegation_id: UUID
    tenant_id: str
    parent_run_id: UUID
    child_run_id: UUID
    # Root execution for Agent Tree. Internally maps to root run_id; externally
    # exposed as root_execution_id. May be None for legacy rows until backfilled.
    root_run_id: UUID | None = None
    parent_agent_id: str
    child_agent_id: str
    capability_token_id: UUID
    delegation_key: str
    child_workflow_id: str | None = None
    status: DelegationStatus = "ISSUED"
    graph_id: str | None = None
    node_id: str | None = None
    depth: int = Field(default=0, ge=0)
    max_depth: int = Field(default=3, ge=1)
    attempt: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=1800, ge=1, le=86_400)
    deadline_at: datetime | None = None
    result_summary: dict[str, Any] | None = None
    error: str | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
