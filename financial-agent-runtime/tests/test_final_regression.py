"""Blocking regression tests for the final hardening pass.

Pure unit tests: no PostgreSQL, Temporal or model endpoint required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from agent_runtime.auth import can_access_run
from agent_runtime.contracts import Principal
from agent_runtime.graph.state_machine import is_terminal_graph
from agent_runtime.memory.service import _not_expired


def _principal(user: str = "u-1", scopes: set[str] | None = None) -> Principal:
    return Principal(
        tenant_id="t-1",
        user_id=user,
        scopes=scopes or set(),
        permissions=set(scopes or set()),
        authentication_method="dev",
    )


# --- Child cancellation: timeout must not be reported as confirmed terminal ---

def test_child_termination_timeout_not_confirmed():
    """A workflow.wait timeout must not set a CANCELLED/TIMED_OUT terminal state."""
    import agent_runtime.orchestration.runner as runner

    handle = object()
    workflow = MagicMock()

    async def wait_timeout(_handles, timeout):
        return (set(), set())  # nothing completed, like a timeout return

    workflow.wait = AsyncMock(side_effect=wait_timeout)
    runner.workflow = workflow

    import asyncio
    confirmed = asyncio.run(runner._await_child_termination(handle, 0.001, 0.001))
    assert confirmed is False


# --- Memory expiry ---

def test_memory_expiry_filter():
    record = MagicMock()
    record.metadata_json = {"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
    assert _not_expired(record) is False
    record.metadata_json = {"expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    assert _not_expired(record) is True
    record.metadata_json = {}
    assert _not_expired(record) is True


# --- Graph terminal state including TIMED_OUT ---

def test_graph_timed_out_is_terminal():
    assert is_terminal_graph("TIMED_OUT") is True
    assert is_terminal_graph("RUNNING") is False


# --- Run ACL invariant ---

def test_run_acl_owner_and_tenant_scope():
    owner = _principal("owner", {"agent:execute"})
    stranger = _principal("stranger", {"agent:execute"})
    auditor = _principal("auditor", {"runs:read:tenant"})
    assert can_access_run(owner, "owner") is True
    assert can_access_run(stranger, "owner") is False
    assert can_access_run(auditor, "owner") is True


# --- Knowledge scope gate ---

def test_knowledge_scope_gate():
    from agent_runtime.knowledge_api import _require_knowledge_scope
    from fastapi import HTTPException

    reader = _principal("reader", {"agent:execute"})
    _require_knowledge_scope(reader)
    with pytest.raises(HTTPException):
        _require_knowledge_scope(reader, write=True)
    writer = _principal("writer", {"agent:execute", "platform:manage"})
    _require_knowledge_scope(writer, write=True)


# --- ApprovalStateChangedError contract ---

def test_approval_state_changed_error_status():
    from agent_runtime.approval.service import ApprovalStateChangedError

    error = ApprovalStateChangedError("approval expired")
    assert error.status_code == 409
    assert error.detail == "approval expired"
