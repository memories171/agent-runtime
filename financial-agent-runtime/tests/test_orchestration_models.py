"""阶段 1：orchestration 模型、ORM 与 contracts 兼容性测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_runtime.contracts import RunCreate, WorkflowInput
from agent_runtime.db import (
    AgentDelegationRecord as DelegationRow,
    CapabilityTokenRecord as TokenRow,
    GraphEventRecord,
    RunEvent,
    RunRecord,
)
from agent_runtime.orchestration import (
    DEFAULT_AGENT_ROLES,
    DELEGATION_TRANSITIONS,
    TERMINAL_DELEGATION_STATUSES,
    AgentDelegationRecord,
    CapabilityGrant,
    CapabilityTokenRecord,
)


def test_agent_roles_and_status_types():
    assert "researcher" in DEFAULT_AGENT_ROLES
    assert "summarizer" in DEFAULT_AGENT_ROLES
    assert TERMINAL_DELEGATION_STATUSES == {
        "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "REVOKED",
    }
    assert "RUNNING" in DELEGATION_TRANSITIONS["ISSUED"] | DELEGATION_TRANSITIONS["CHILD_STARTING"]
    assert DELEGATION_TRANSITIONS["COMPLETED"] == frozenset()
    # CANCEL_REQUESTED 非终态：可收敛到任一终态，但不可再回到 RUNNING。
    assert "CANCEL_REQUESTED" not in TERMINAL_DELEGATION_STATUSES
    assert "CANCEL_REQUESTED" in DELEGATION_TRANSITIONS["RUNNING"]
    assert "CANCEL_REQUESTED" in DELEGATION_TRANSITIONS["WAITING"]
    assert {"CANCELLED", "TIMED_OUT", "COMPLETED", "FAILED"} <= DELEGATION_TRANSITIONS["CANCEL_REQUESTED"]
    assert "RUNNING" not in DELEGATION_TRANSITIONS["CANCEL_REQUESTED"]


def test_capability_grant_defaults_and_validation():
    grant = CapabilityGrant()
    assert grant.capabilities == set()
    assert grant.max_depth == 1
    assert grant.timeout_seconds == 1800
    assert CapabilityGrant(capabilities={"agent:execute"}).capabilities == {"agent:execute"}


def test_capability_token_status():
    now = datetime.now(timezone.utc)
    base = dict(
        token_id=uuid4(), tenant_id="t", run_id=uuid4(),
        capabilities={"agent:execute"}, signature_hash="sig",
    )
    active = CapabilityTokenRecord(**base, expires_at=now + timedelta(hours=1))
    assert active.status == "ACTIVE"
    expired = CapabilityTokenRecord(**base, expires_at=now - timedelta(seconds=1))
    assert expired.status == "EXPIRED"
    revoked = CapabilityTokenRecord(
        **base, expires_at=now + timedelta(hours=1), revoked_at=now,
    )
    assert revoked.status == "REVOKED"


def test_delegation_record_defaults():
    delegated = AgentDelegationRecord(
        delegation_id=uuid4(), tenant_id="t", parent_run_id=uuid4(), child_run_id=uuid4(),
        parent_agent_id="parent", child_agent_id="researcher",
        capability_token_id=uuid4(), delegation_key="delegation-1",
    )
    assert delegated.status == "ISSUED"
    assert delegated.depth == 0
    assert delegated.max_depth == 3
    assert delegated.attempt == 1
    assert delegated.artifact_refs == []


def test_contracts_backward_compatible():
    old = RunCreate(prompt="hello")
    assert old.graph is None
    assert old.orchestration is None
    configured = RunCreate(prompt="hello", orchestration={"enabled": True, "max_depth": 2})
    assert configured.orchestration["max_depth"] == 2

    old_input = WorkflowInput(
        run_id="r", tenant_id="t", user_id="u", session_id="s", agent_id="a",
        prompt="hello", scopes=[], max_steps=5, max_duration_seconds=60,
    )
    assert old_input.run_kind == "ROOT"
    assert old_input.parent_run_id is None
    assert old_input.delegation_id is None
    assert old_input.capability_token_id is None
    assert old_input.agent_role is None

    child_input = WorkflowInput(
        run_id="r2", tenant_id="t", user_id="u", session_id="s", agent_id="a",
        prompt="hello", scopes=[], max_steps=5, max_duration_seconds=60,
        run_kind="SUB_AGENT", parent_run_id="r", delegation_id="d",
        capability_token_id="token", agent_role="researcher",
    )
    assert child_input.run_kind == "SUB_AGENT"
    assert child_input.agent_role == "researcher"


def test_orm_tables_and_constraints():
    assert DelegationRow.__tablename__ == "agent_delegations"
    assert TokenRow.__tablename__ == "capability_tokens"

    token_columns = set(TokenRow.__table__.columns.keys())
    assert {"token_id", "tenant_id", "parent_token_id", "capabilities", "expires_at", "signature_hash"} <= token_columns
    delegation_columns = set(DelegationRow.__table__.columns.keys())
    assert {"delegation_id", "parent_run_id", "child_run_id", "capability_token_id", "delegation_key"} <= delegation_columns

    token_names = {c.name for c in TokenRow.__table__.constraints}
    delegation_names = {c.name for c in DelegationRow.__table__.constraints}
    assert "uq_capability_token_tenant_token" in token_names
    assert "uq_agent_delegation_key" in delegation_names
    assert "uq_agent_delegation_node_attempt" in delegation_names

    assert "ix_capability_token_subject" in {i.name for i in TokenRow.__table__.indexes}
    assert "ix_agent_delegation_parent" in {i.name for i in DelegationRow.__table__.indexes}

    assert "run_kind" in RunRecord.__table__.columns
    assert "parent_run_id" in RunEvent.__table__.columns
    assert "delegation_id" in GraphEventRecord.__table__.columns
    assert "capability_token_id" in GraphEventRecord.__table__.columns
