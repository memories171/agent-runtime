"""B2: duration <-> max_steps coupling, hard cap, graph step limit, replay safety."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_runtime.api import MAX_STEPS_HARD_LIMIT, resolve_step_budget
from agent_runtime.config import settings
from agent_runtime.contracts import RunCreate, WorkflowInput


SRC = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"


# --- pure resolver ---------------------------------------------------------
def test_short_task_keeps_requested_steps():
    # 5-min task needs ~5 steps; explicit 12 is kept, no adjustment.
    steps, adj = resolve_step_budget(
        duration_seconds=300, requested_steps=12, default_steps=50, seconds_per_step=60
    )
    assert steps == 12
    assert adj is None


def test_thirty_minute_task_gets_reasonable_budget():
    # 1800s / 60 = 30 steps required; default 50 already covers it.
    steps, adj = resolve_step_budget(
        duration_seconds=1800, requested_steps=None, default_steps=50, seconds_per_step=60
    )
    assert steps == 50
    assert adj is None
    # A low explicit value is raised to the duration-derived need with audit.
    steps, adj = resolve_step_budget(
        duration_seconds=1800, requested_steps=10, default_steps=50, seconds_per_step=60
    )
    assert steps == 30
    assert adj == {
        "original": 10, "adjusted": 30, "reason": "duration_budget",
        "duration_seconds": 1800, "required_steps": 30,
    }


def test_long_task_auto_upgrades_and_records():
    # 2h / 60 = 120 required; default 50 -> raised to 120, capped under 200.
    steps, adj = resolve_step_budget(
        duration_seconds=7200, requested_steps=None, default_steps=50, seconds_per_step=60
    )
    assert steps == 120
    assert adj["original"] == 50 and adj["adjusted"] == 120
    assert adj["reason"] == "duration_budget"


def test_step_cap_never_exceeded_without_error():
    # Requested 200 with short duration stays at the hard cap.
    steps, adj = resolve_step_budget(
        duration_seconds=600, requested_steps=200, default_steps=50, seconds_per_step=60
    )
    assert steps == MAX_STEPS_HARD_LIMIT == 200
    assert adj is None


def test_required_steps_over_hard_cap_raises_for_http_400():
    # 4h / 60 = 240 > 200 -> ValueError mapped to HTTP 400 by API.
    with pytest.raises(ValueError, match="exceeding the hard limit"):
        resolve_step_budget(
            duration_seconds=14400, requested_steps=None, default_steps=50, seconds_per_step=60
        )


def test_deterministic_budget_across_durations():
    # Same inputs always produce the same budget (no hidden state / no inconsistency).
    for duration in (300, 900, 1800, 3600, 7200, 12000):
        a = resolve_step_budget(duration, None, 50, 60)
        b = resolve_step_budget(duration, None, 50, 60)
        assert a == b
    # Longer duration never yields fewer steps.
    budgets = [resolve_step_budget(d, None, 50, 60)[0] for d in (300, 900, 1800, 3600, 7200)]
    assert budgets == sorted(budgets)


def test_invalid_seconds_per_step_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        resolve_step_budget(1800, None, 50, 0)


# --- contracts / config ----------------------------------------------------
def test_run_create_hard_cap_contract():
    with pytest.raises(Exception):
        RunCreate(prompt="x", max_steps=201)
    assert RunCreate(prompt="x", max_steps=200).max_steps == 200


def test_workflow_input_frozen_grace_field():
    fields = WorkflowInput.model_fields
    assert "child_cancel_grace_seconds" in fields
    assert fields["child_cancel_grace_seconds"].default is None


# --- graph step limit ------------------------------------------------------
def test_graph_uses_validated_limits_max_node_steps():
    src = (SRC / "graph" / "runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Assert the sub-agent state no longer hardcodes settings.graph_max_node_steps
    assert '"max_steps": settings.graph_max_node_steps' not in src
    assert '"max_steps": limits.max_node_steps' in src


# --- replay safety ---------------------------------------------------------
def test_workflow_no_nondeterministic_time_or_id_calls():
    src = (SRC / "workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"uuid4", "time", "monotonic", "random"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned, f"bare {node.func.id}() in workflow.py"


def test_settings_read_in_workflow_is_guarded_by_frozen_input():
    src = (SRC / "workflow.py").read_text(encoding="utf-8")
    assert "run_input.child_cancel_grace_seconds" in src
    # The fallback to settings must be guarded by a None check (old histories).
    assert "is not None" in src


# --- HTTP 400 ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_run_over_cap_duration_returns_http_400():
    """An over-budget duration maps the resolver's ValueError to HTTP 400."""
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import HTTPException

    from agent_runtime.api import create_run
    from agent_runtime.contracts import Principal, RunCreate

    principal = Principal(
        tenant_id="tenant-a", user_id="user-a",
        scopes={"agent:execute"}, permissions={"agent:execute"},
        authentication_method="dev",
    )
    body = RunCreate(prompt="long task", max_duration_seconds=14400)  # 4h -> 240 > 200
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await create_run(body=body, principal=principal, session=session)
    assert exc.value.status_code == 400
    assert "hard limit" in exc.value.detail
