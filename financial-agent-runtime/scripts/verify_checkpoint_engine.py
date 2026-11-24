"""Checkpoint Engine 第二步验证：快照完整性、加密压缩、幂等、租户隔离、损坏拒绝。

在 api 容器内运行（含真实 PostgreSQL）。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import select

from agent_runtime.checkpoint.crypto import canonical_json_bytes, sha256_hex
from agent_runtime.checkpoint.service import (
    CheckpointSnapshotService,
    SnapshotIntegrityError,
)
from agent_runtime.checkpoint.storage import LocalSnapshotStore
from agent_runtime.db import (
    CheckpointRecord,
    RunRecord,
    SessionLocal,
    append_event,
    session_for_tenant,
)
from agent_runtime.rls import set_tenant_context

TENANT = "demo-bank"
OTHER_TENANT = "tenant-b"
USER = "analyst-01"


def make_service(root: str) -> CheckpointSnapshotService:
    return CheckpointSnapshotService(LocalSnapshotStore(root), "", "dev-local")


def sample_state(step: int) -> dict:
    return {
        "run_id": "verification-run",
        "tenant_id": TENANT,
        "user_id": USER,
        "prompt": "核对账户 ACC-DEMO-001",
        "history": [
            {"type": "user", "content": "请核对账户"},
            {"type": "tool_result", "tool": "demo.echo", "result": {"ok": True}},
        ],
        "tool_calls": [{"name": "demo.echo", "arguments": {"text": "hi"}}],
        "token_input": 100 * step,
        "token_output": 20 * step,
        "estimated_cost_usd": 0.001 * step,
        "current_step": f"step-{step}",
        "status": "RUNNING",
        "max_duration_seconds": 600,
        "remaining_steps": 10 - step,
        "current_step_state": {"step": step},
        "principal": {"user_id": USER, "tenant_id": TENANT, "access_token": "MUST-NOT-APPEAR"},
    }


async def seed_run(session, run_id, tenant=TENANT):
    session.add(RunRecord(
        run_id=run_id, tenant_id=tenant, user_id=USER,
        session_id=f"verify-{run_id}", agent_id="default-agent",
        prompt="checkpoint verification", status="RUNNING", metadata_json={},
    ))
    await session.commit()


async def main():
    run_id = uuid4()
    checkpoint_id = str(uuid4())
    service = make_service("./.snapshots-verify")

    async with session_for_tenant(TENANT) as seed_session:
        await seed_run(seed_session, run_id)

    async with session_for_tenant(TENANT) as session:
        envelope = await service.persist(
            session, CheckpointRecord, append_event,
            payload={
                "checkpoint_id": checkpoint_id,
                "run_id": str(run_id),
                "tenant_id": TENANT,
                "step": 3,
                "summary": "验证完整快照",
                "worker_id": "verify-worker",
                "idempotency_key": f"{run_id}:checkpoint:3",
                "state": sample_state(3),
                "workflow_id": f"agent-run/{TENANT}/{run_id}",
                "workflow_run_id": str(uuid4()),
            },
        )
        print("persist_status", envelope.persistence_status)
        print("schema_version", envelope.schema_version)
        print("state_digest_len", len(envelope.state_digest))
        print("blob_size_bytes", envelope.blob_size_bytes)

    async with session_for_tenant(TENANT) as session:
        loaded, snapshot = await service.load(
            session, CheckpointRecord, tenant_id=TENANT, run_id=str(run_id), sequence=3,
        )
        print("load_current_step", snapshot.execution_state.current_step)
        print("load_objective", snapshot.plan_state.objective)
        print("load_history_len", len(snapshot.history))
        print("load_used_tokens", snapshot.budget_state.used_tokens)
        print("load_digest_match", loaded.state_digest == envelope.state_digest)
        print("secret_scrubbed", "access_token" not in snapshot.principal)

    events = []
    async with session_for_tenant(TENANT) as session:
        from agent_runtime.db import RunEvent
        rows = (await session.execute(
            select(RunEvent.event_type).where(RunEvent.run_id == run_id)
        )).scalars()
        events = list(rows)
    print("checkpoint_created_event", "CHECKPOINT_CREATED" in events)
    print("checkpoint_saved_event", "CHECKPOINT_SAVED" in events)

    # 幂等：相同 sequence + 相同 digest 直接返回
    async with session_for_tenant(TENANT) as session:
        idem = await service.persist(
            session, CheckpointRecord, append_event,
            payload={
                "checkpoint_id": str(uuid4()),
                "run_id": str(run_id),
                "tenant_id": TENANT,
                "step": 3,
                "summary": "重复写",
                "idempotency_key": f"{run_id}:checkpoint:3-repeat",
                "state": sample_state(3),
            },
        )
        print("idempotent_checkpoint_id", idem.checkpoint_id == checkpoint_id)

    # 幂等但不同 digest：拒绝
    async with session_for_tenant(TENANT) as session:
        try:
            await service.persist(
                session, CheckpointRecord, append_event,
                payload={
                    "checkpoint_id": str(uuid4()),
                    "run_id": str(run_id),
                    "tenant_id": TENANT,
                    "step": 3,
                    "summary": "冲突写",
                    "idempotency_key": f"{run_id}:checkpoint:3-conflict",
                    "state": sample_state(99),
                },
            )
            print("conflict_rejected", False)
        except SnapshotIntegrityError:
            print("conflict_rejected", True)

    # 租户隔离：跨租户读不到
    async with session_for_tenant(OTHER_TENANT) as session:
        try:
            await service.load(
                session, CheckpointRecord, tenant_id=OTHER_TENANT, run_id=str(run_id), sequence=3,
            )
            print("tenant_isolation", False)
        except Exception as exc:
            print("tenant_isolation", type(exc).__name__)

    # 损坏快照：篡改对象后拒绝恢复
    async with session_for_tenant(TENANT) as session:
        row = (await session.execute(
            select(CheckpointRecord).where(CheckpointRecord.checkpoint_id == __import__("uuid").UUID(checkpoint_id))
        )).scalar_one()
        ref = row.state_blob_ref
    store = LocalSnapshotStore("./.snapshots-verify")
    original = await store.get(ref)
    await store.put(ref, original[:-1] + bytes([original[-1] ^ 0xFF]))
    async with session_for_tenant(TENANT) as session:
        try:
            await service.load(
                session, CheckpointRecord, tenant_id=TENANT, run_id=str(run_id), sequence=3,
            )
            print("corruption_rejected", False)
        except SnapshotIntegrityError:
            print("corruption_rejected", True)


asyncio.run(main())
