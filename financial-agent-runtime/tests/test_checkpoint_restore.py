from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.checkpoint.builder import build_state_snapshot
from agent_runtime.checkpoint.models import CheckpointEnvelope
from agent_runtime.checkpoint.restore_contract import (
    RESTORE_CONTRACT_VERSION,
    RestoreContract,
    build_restore_contract,
    inject_restore_state,
)


def _sample_snapshot():
    state = {
        "run_id": "run-1",
        "tenant_id": "tenant-a",
        "prompt": "核对账户 ACC-001",
        "history": [
            {"type": "user", "content": "hello"},
            {"type": "tool_result", "tool": "demo.echo", "call_id": "tc-1", "result": {"ok": True}},
            {"type": "tool_result", "tool": "demo.noresult", "call_id": "tc-2"},
        ],
        "tool_calls": [{"call_id": "tc-1", "tool": "demo.echo", "status": "SUCCESS"}],
        "llm_calls": [{"call_id": "llm-1"}],
        "token_input": 120,
        "token_output": 40,
        "estimated_cost_usd": 0.0005,
        "principal": {
            "user_id": "u-1", "tenant_id": "tenant-a", "access_token": "SECRET",
            "organization_id": "org-1", "roles": ["analyst"], "permissions": ["read"],
        },
        "current_step": "step-2",
        "completed_steps": [{"id": "s1", "status": "SUCCESS"}],
        "status": "RUNNING",
        "max_duration_seconds": 600,
        "remaining_steps": 8,
        "context_event_cursor": 7,
        "context_summary": "summary-v1",
    }
    return build_state_snapshot(state)


def _envelope():
    return CheckpointEnvelope(
        checkpoint_id="ckpt-1",
        tenant_id="tenant-a",
        run_id="run-1",
        temporal_workflow_id="wf-1",
        temporal_execution_id="exec-1",
        checkpoint_sequence=3,
        created_at=datetime.now(timezone.utc),
        state_digest="digest-abc",
        blob_sha256="blob-abc",
        blob_size_bytes=1024,
        state_blob_ref="ref-abc",
        encryption_key_id="k1",
        persistence_status="AVAILABLE_FOR_RECOVERY",
    )


def test_restore_contract_version_and_source():
    contract = build_restore_contract(
        envelope=_envelope(),
        state=_sample_snapshot(),
        source_run_id="run-1",
        restored_by_user_id="u-1",
        capability_token_id="cap-1",
    )
    assert contract.version == RESTORE_CONTRACT_VERSION
    assert contract.source.checkpoint_id == "ckpt-1"
    assert contract.source.source_run_id == "run-1"
    assert contract.source.tenant_id == "tenant-a"
    assert contract.source.state_digest == "digest-abc"
    assert contract.capability_token_id == "cap-1"


def test_confirmed_tool_results_exclude_no_result_edge_case():
    contract = build_restore_contract(
        envelope=_envelope(), state=_sample_snapshot(),
        source_run_id="run-1", restored_by_user_id="u-1",
    )
    # demo.echo has a result => reusable; demo.noresult has no result => never reused.
    assert "demo.echo:tc-1" in contract.confirmed_tool_results
    assert "demo.noresult:tc-2" not in contract.confirmed_tool_results


def test_state_injection_restores_business_state_only():
    contract = build_restore_contract(
        envelope=_envelope(), state=_sample_snapshot(),
        source_run_id="run-1", restored_by_user_id="u-1",
    )
    fresh_state = {
        "run_id": "run-NEW", "tenant_id": "tenant-a",
        "principal": {"tenant_id": "tenant-a", "user_id": "u-NEW", "scopes": ["agent:execute"]},
        "history": [], "tool_calls": [], "llm_calls": [],
    }
    injected = inject_restore_state(fresh_state, contract)
    assert injected["run_id"] == "run-NEW"         # new run identity preserved
    assert injected["history"][0]["type"] == "user"
    assert len(injected["tool_calls"]) == 1
    assert injected["completed_steps"] == [{"id": "s1", "status": "SUCCESS"}]
    assert injected["context_event_cursor"] == 7
    # budget_state stores combined used_tokens (input+output) => restored total.
    assert injected["token_input"] == 160
    assert injected["principal"]["user_id"] == "u-NEW"   # caller identity kept
    assert injected["principal"]["organization_id"] == "org-1"  # capability parity
    assert injected["restore_contract"]["version"] == RESTORE_CONTRACT_VERSION


def test_restore_contract_round_trip_pydantic():
    contract = build_restore_contract(
        envelope=_envelope(), state=_sample_snapshot(),
        source_run_id="run-1", restored_by_user_id="u-1",
    )
    restored = RestoreContract.model_validate(contract.model_dump(mode="json"))
    assert restored.source.checkpoint_id == "ckpt-1"
    assert "demo.echo:tc-1" in restored.confirmed_tool_results


def test_restore_path_uses_workflow_patched_for_replay_compat():
    """B1-style static guard: restore injection must be under workflow.patched."""
    src = Path(__file__).parents[1] / "src" / "agent_runtime" / "workflow.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found_patched = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "patched" and isinstance(node.func.value, ast.Name) and node.func.value.id == "workflow":
                found_patched = True
    assert found_patched, "restore injection must be guarded by workflow.patched"


def test_restore_side_effect_reuse_branch_exists():
    """Static guard: the tool branch must check restored confirmed keys."""
    src = Path(__file__).parents[1] / "src" / "agent_runtime" / "workflow.py"
    text = src.read_text(encoding="utf-8")
    assert "_restored_confirmed_keys" in text
    assert "reuse_key in confirmed" in text
    assert 'event_type": "TOOL_RESULT_RESTORED"' in text
