from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from agent_runtime.checkpoint.builder import build_envelope_fields, build_state_snapshot
from agent_runtime.checkpoint.crypto import (
    canonical_json_bytes,
    compress,
    decrypt,
    decompress,
    encrypt,
    resolve_encryption_key,
    sha256_hex,
)
from agent_runtime.checkpoint.models import (
    CHECKPOINT_SCHEMA_VERSION,
    LEGACY_CHECKPOINT_SCHEMA_VERSION,
    CheckpointEnvelope,
    SnapshotPayload,
)
from agent_runtime.checkpoint.storage import SnapshotStore

logger = logging.getLogger(__name__)


class SnapshotIntegrityError(RuntimeError):
    pass


class SnapshotNotFoundError(RuntimeError):
    pass


class UnsupportedSnapshotVersion(RuntimeError):
    pass


class CheckpointSnapshotService:
    """Checkpoint 完整生命周期：序列化 → 压缩 → 加密 → 上传 → 校验 → 持久化。

    PostgreSQL 只存 Envelope 元数据与对象引用；完整 Agent State 存在对象存储。
    """

    def __init__(self, store: SnapshotStore, encryption_keys_json: str, default_key_id: str) -> None:
        self._store = store
        self._encryption_keys_json = encryption_keys_json
        self._default_key_id = default_key_id

    def _key(self, key_id: str) -> bytes:
        return resolve_encryption_key(key_id, self._encryption_keys_json)

    @staticmethod
    def _object_ref(envelope: CheckpointEnvelope) -> str:
        return (
            f"tenants/{envelope.tenant_id}/runs/{envelope.run_id}/checkpoints/"
            f"{envelope.checkpoint_id}.snapshot"
        )

    async def serialize(self, state: dict[str, Any]) -> tuple[str, bytes]:
        snapshot = build_state_snapshot(state)
        payload = SnapshotPayload(checkpoint_id="", state=snapshot)
        raw = canonical_json_bytes(payload.model_dump(mode="json"))
        return sha256_hex(raw), raw

    async def persist(
        self,
        session: Any,
        checkpoint_model: Any,
        event_appender: Any,
        *,
        payload: dict[str, Any],
    ) -> CheckpointEnvelope:
        """幂等持久化完整快照。

        payload 必须包含 checkpoint_id / tenant_id / run_id / step / state / workflow_id 等。
        同一 (run_id, checkpoint_sequence) 已存在且 digest 一致时直接返回已有 Envelope。
        """
        from agent_runtime.checkpoint.models import CheckpointPersistenceStatus

        state = dict(payload.get("state") or {})
        checkpoint_id = str(payload["checkpoint_id"])
        tenant_id = str(payload["tenant_id"])
        run_id = str(payload["run_id"])
        step = int(payload["step"])
        state["checkpoint_id"] = checkpoint_id

        state_digest, raw = await self.serialize(state)
        key_id = str(payload.get("encryption_key_id") or self._default_key_id)
        key = self._key(key_id)
        compressed = compress(raw)
        ciphertext, nonce = encrypt(compressed, key)

        # 存储格式：magic(8) + nonce(12) + ciphertext；digest 覆盖最终 blob 与明文 state。
        blob = b"FINORA01" + nonce + ciphertext
        blob_sha256 = sha256_hex(blob)

        existing = (await session.execute(
            select(checkpoint_model).where(
                checkpoint_model.tenant_id == tenant_id,
                checkpoint_model.run_id == run_id,
                checkpoint_model.checkpoint_sequence == step,
            )
        )).scalars().first()
        if existing is not None:
            if existing.state_digest == state_digest and existing.persistence_status in {
                "PERSISTED", "AVAILABLE_FOR_RECOVERY"
            }:
                return CheckpointEnvelope.model_validate(existing.envelope_json)
            raise SnapshotIntegrityError(
                f"checkpoint sequence {step} already exists with a different digest"
            )

        snapshot = build_state_snapshot(state)
        fields = build_envelope_fields(
            checkpoint_id, tenant_id, run_id, step, state, snapshot,
            temporal_workflow_id=str(payload.get("workflow_id", "")),
            temporal_execution_id=str(payload.get("workflow_run_id", "")),
            checkpoint_sequence=step,
        )
        envelope = CheckpointEnvelope(
            **fields,
            state_digest=state_digest,
            blob_sha256=blob_sha256,
            blob_size_bytes=len(blob),
            state_blob_ref="pending",
            encryption_key_id=key_id,
            persistence_status="SERIALIZING",
        )
        envelope.state_blob_ref = self._object_ref(envelope)
        envelope.persistence_status = "UPLOADING"

        record = checkpoint_model(
            checkpoint_id=envelope.checkpoint_id,
            run_id=envelope.run_id,
            tenant_id=envelope.tenant_id,
            step=step,
            checkpoint_sequence=step,
            summary=str(payload.get("summary", "")),
            size_kb=max(1, len(blob) // 1024),
            status="UPLOADING",
            worker_id=str(payload.get("worker_id", "temporal-worker")),
            idempotency_key=str(payload.get("idempotency_key", f"{run_id}:checkpoint:{step}")),
            state_digest=envelope.state_digest,
            blob_sha256=envelope.blob_sha256,
            blob_size_bytes=envelope.blob_size_bytes,
            state_blob_ref=envelope.state_blob_ref,
            schema_version=envelope.schema_version,
            compression_algorithm=envelope.compression_algorithm,
            encryption_algorithm=envelope.encryption_algorithm,
            encryption_key_id=envelope.encryption_key_id,
            envelope_json=envelope.model_dump(mode="json"),
            payload_json={"history_length": len(snapshot.history), "step": step},
            persistence_status="UPLOADING",
        )
        session.add(record)

        uploaded = False
        try:
            await self._store.put(envelope.state_blob_ref, blob)
            uploaded = True
            envelope.persistence_status = "VERIFYING"
            record.persistence_status = "VERIFYING"
            fetched = await self._store.get(envelope.state_blob_ref)
            if sha256_hex(fetched) != blob_sha256:
                raise SnapshotIntegrityError("uploaded snapshot digest mismatch")
            restored_raw = decompress(decrypt(fetched[8 + 12:], fetched[8:8 + 12], key), envelope.compression_algorithm)
            if sha256_hex(restored_raw) != state_digest:
                raise SnapshotIntegrityError("snapshot state digest mismatch after round-trip")
            SnapshotPayload.model_validate_json(restored_raw.decode("utf-8"))
            envelope.persistence_status = "AVAILABLE_FOR_RECOVERY"
            record.persistence_status = "AVAILABLE_FOR_RECOVERY"
            record.envelope_json = envelope.model_dump(mode="json")
            await event_appender(
                session, envelope.run_id, envelope.tenant_id, "CHECKPOINT_CREATED",
                {"checkpoint_id": envelope.checkpoint_id, "step": step, "state_digest": state_digest},
            )
            await event_appender(
                session, envelope.run_id, envelope.tenant_id, "CHECKPOINT_SAVED",
                {"checkpoint_id": envelope.checkpoint_id, "step": step, "schema_version": envelope.schema_version},
            )
            await session.commit()
            return envelope
        except Exception as exc:
            await session.rollback()
            if uploaded:
                try:
                    await self._store.delete(envelope.state_blob_ref)
                except Exception:
                    logger.warning("failed to clean up orphan snapshot object %s", envelope.state_blob_ref)
            logger.exception("checkpoint persistence failed for run=%s step=%s", run_id, step)
            raise

    async def load(
        self,
        session: Any,
        checkpoint_model: Any,
        *,
        tenant_id: str,
        run_id: str,
        checkpoint_id: str | None = None,
        sequence: int | None = None,
    ) -> tuple[CheckpointEnvelope, AgentStateSnapshot]:
        query = select(checkpoint_model).where(
            checkpoint_model.tenant_id == tenant_id,
            checkpoint_model.run_id == run_id,
        )
        if checkpoint_id is not None:
            query = query.where(checkpoint_model.checkpoint_id == checkpoint_id)
        if sequence is not None:
            query = query.where(checkpoint_model.checkpoint_sequence == sequence)
        query = query.order_by(checkpoint_model.checkpoint_sequence.desc())
        record = (await session.execute(query)).scalars().first()
        if record is None:
            raise SnapshotNotFoundError("checkpoint not found")
        if record.persistence_status not in {"PERSISTED", "AVAILABLE_FOR_RECOVERY"}:
            raise SnapshotIntegrityError(f"checkpoint is not available: {record.persistence_status}")
        if record.schema_version and record.schema_version != CHECKPOINT_SCHEMA_VERSION:
            if record.schema_version == LEGACY_CHECKPOINT_SCHEMA_VERSION:
                raise UnsupportedSnapshotVersion("legacy checkpoint payload has no recoverable state snapshot")
            raise UnsupportedSnapshotVersion(f"unsupported checkpoint schema version: {record.schema_version}")

        envelope = CheckpointEnvelope.model_validate(record.envelope_json)
        blob = await self._store.get(envelope.state_blob_ref)
        if sha256_hex(blob) != envelope.blob_sha256:
            raise SnapshotIntegrityError("stored snapshot digest mismatch")
        key = self._key(envelope.encryption_key_id)
        raw = decompress(decrypt(blob[8 + 12:], blob[8:8 + 12], key), envelope.compression_algorithm)
        if sha256_hex(raw) != envelope.state_digest:
            raise SnapshotIntegrityError("snapshot state digest mismatch")
        payload = SnapshotPayload.model_validate_json(raw.decode("utf-8"))
        if payload.checkpoint_id and payload.checkpoint_id != envelope.checkpoint_id:
            raise SnapshotIntegrityError("snapshot checkpoint id mismatch")
        return envelope, payload.state
