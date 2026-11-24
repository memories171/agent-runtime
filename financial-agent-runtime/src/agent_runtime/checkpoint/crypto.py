from __future__ import annotations

import hashlib
import json
import os
import zlib
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_runtime.checkpoint.models import (
    COMPRESSION_ALGORITHMS,
    ENCRYPTION_ALGORITHMS,
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compress(data: bytes, algorithm: str = "zlib") -> bytes:
    if algorithm not in COMPRESSION_ALGORITHMS:
        raise ValueError(f"unsupported compression algorithm: {algorithm}")
    return zlib.compress(data, level=6)


def decompress(data: bytes, algorithm: str = "zlib") -> bytes:
    if algorithm not in COMPRESSION_ALGORITHMS:
        raise ValueError(f"unsupported compression algorithm: {algorithm}")
    return zlib.decompress(data)


def resolve_encryption_key(key_id: str, keys_json: str) -> bytes:
    """Keyring 解析：key_id 对应 base64 编码的 32 字节 key。

    keys_json 形如 {"key-2026-01": "<base64>"}。未配置时仅允许 development/test，
    使用固定派生 key，并在日志中通过配置校验提示。
    """
    if keys_json:
        try:
            keyring = json.loads(keys_json)
        except json.JSONDecodeError as exc:
            raise ValueError("SNAPSHOT_ENCRYPTION_KEYS is not valid JSON") from exc
        if not isinstance(keyring, dict) or key_id not in keyring:
            raise ValueError(f"snapshot encryption key id not found: {key_id}")
        key = base64_decode(str(keyring[key_id]))
        if len(key) != 32:
            raise ValueError("snapshot encryption key must be 32 bytes")
        return key
    if key_id not in {"", "dev-local"}:
        raise ValueError(f"snapshot encryption key id {key_id!r} requires SNAPSHOT_ENCRYPTION_KEYS")
    return hashlib.sha256(b"finora-dev-snapshot-key-v1").digest()


def base64_decode(value: str) -> bytes:
    import base64

    return base64.b64decode(value, validate=True)


def base64_encode(value: bytes) -> str:
    import base64

    return base64.b64encode(value).decode("ascii")


def encrypt(plaintext: bytes, key: bytes, algorithm: str = "AES-256-GCM") -> tuple[bytes, bytes]:
    if algorithm not in ENCRYPTION_ALGORITHMS:
        raise ValueError(f"unsupported encryption algorithm: {algorithm}")
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = os.urandom(12)
    return AESGCM(key).encrypt(nonce, plaintext, None), nonce


def decrypt(ciphertext: bytes, nonce: bytes, key: bytes, algorithm: str = "AES-256-GCM") -> bytes:
    if algorithm not in ENCRYPTION_ALGORITHMS:
        raise ValueError(f"unsupported encryption algorithm: {algorithm}")
    if len(nonce) != 12:
        raise ValueError("invalid AES-GCM nonce length")
    return AESGCM(key).decrypt(nonce, ciphertext, None)
