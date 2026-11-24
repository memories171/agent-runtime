from __future__ import annotations

import asyncio

import pytest

from agent_runtime.checkpoint.builder import build_envelope_fields, build_state_snapshot
from agent_runtime.checkpoint.crypto import (
    canonical_json_bytes,
    compress,
    decrypt,
    decompress,
    encrypt,
    sha256_hex,
)
from agent_runtime.checkpoint.models import CheckpointEnvelope, SnapshotPayload
from agent_runtime.checkpoint.storage import LocalSnapshotStore


def sample_state():
    return {
        "run_id": "run-1",
        "tenant_id": "tenant-a",
        "prompt": "核对账户 ACC-001",
        "history": [{"type": "user", "content": "hello"}, {"type": "tool_result", "tool": "demo.echo", "result": {"ok": True}}],
        "tool_calls": [{"name": "demo.echo", "arguments": {"text": "hi"}}],
        "llm_calls": [{"call_id": "llm-1"}],
        "token_input": 120,
        "token_output": 40,
        "estimated_cost_usd": 0.0005,
        "principal": {"user_id": "u-1", "tenant_id": "tenant-a", "access_token": "SECRET", "api_key": "SECRET"},
        "current_step": "step-2",
        "status": "RUNNING",
        "max_duration_seconds": 600,
        "remaining_steps": 8,
    }


def test_snapshot_scrubs_secrets():
    snapshot = build_state_snapshot(sample_state())
    assert "access_token" not in snapshot.principal
    assert "api_key" not in snapshot.principal
    assert snapshot.principal["user_id"] == "u-1"


def test_snapshot_has_full_state():
    state = sample_state()
    snapshot = build_state_snapshot(state)
    assert snapshot.plan_state.objective == state["prompt"]
    assert snapshot.execution_state.current_step == "step-2"
    assert len(snapshot.history) == 2
    assert snapshot.budget_state.used_tokens == 160
    assert snapshot.budget_state.used_cost_usd == 0.0005
    assert len(snapshot.context_state.recent_messages) == 2
    assert snapshot.context_state.memory_references == []


def test_envelope_fields_complete():
    state = sample_state()
    snapshot = build_state_snapshot(state)
    fields = build_envelope_fields("cp-1", "tenant-a", "run-1", 2, state, snapshot, checkpoint_sequence=2)
    envelope = CheckpointEnvelope(
        **fields,
        state_digest="abc",
        blob_sha256="def",
        blob_size_bytes=100,
        state_blob_ref="tenants/tenant-a/runs/run-1/checkpoints/cp-1.snapshot",
    )
    assert envelope.schema_version == "2.0"
    assert envelope.checkpoint_sequence == 2
    assert envelope.current_step_id == "step-2"
    assert envelope.token_usage["total"] == 160
    assert envelope.execution_budget["max_duration_seconds"] == 600
    assert envelope.persistence_status == "CREATED"


def test_encrypt_compress_digest_roundtrip():
    payload = SnapshotPayload(checkpoint_id="cp-1", state=build_state_snapshot(sample_state()))
    raw = canonical_json_bytes(payload.model_dump(mode="json"))
    digest = sha256_hex(raw)
    key = b"0" * 32
    ciphertext, nonce = encrypt(compress(raw), key)
    restored_raw = decompress(decrypt(ciphertext, nonce, key))
    assert sha256_hex(restored_raw) == digest
    restored = SnapshotPayload.model_validate_json(restored_raw.decode("utf-8"))
    assert restored.checkpoint_id == "cp-1"
    assert restored.state.execution_state.current_step == "step-2"


def test_corrupted_blob_rejected():
    raw = canonical_json_bytes({"checkpoint_id": "cp-1", "state": {}})
    key = b"0" * 32
    ciphertext, nonce = encrypt(raw, key)
    corrupted = ciphertext[:-1] + bytes([ciphertext[-1] ^ 0xFF])
    with pytest.raises(Exception):
        decrypt(corrupted, nonce, key)


@pytest.mark.asyncio
async def test_local_store_path_traversal_rejected():
    store = LocalSnapshotStore("./.snapshots-test")
    with pytest.raises(ValueError):
        await store.put("../escape.snapshot", b"data")


@pytest.mark.asyncio
async def test_local_store_roundtrip(tmp_path):
    store = LocalSnapshotStore(str(tmp_path))
    await store.put("tenants/t1/runs/r1/checkpoints/c1.snapshot", b"payload")
    assert await store.exists("tenants/t1/runs/r1/checkpoints/c1.snapshot")
    assert await store.get("tenants/t1/runs/r1/checkpoints/c1.snapshot") == b"payload"
    await store.delete("tenants/t1/runs/r1/checkpoints/c1.snapshot")
    assert not await store.exists("tenants/t1/runs/r1/checkpoints/c1.snapshot")
