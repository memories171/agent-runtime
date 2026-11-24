from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class SnapshotStore(ABC):
    @abstractmethod
    async def put(self, ref: str, data: bytes) -> None: ...

    @abstractmethod
    async def get(self, ref: str) -> bytes: ...

    @abstractmethod
    async def delete(self, ref: str) -> None: ...

    @abstractmethod
    async def exists(self, ref: str) -> bool: ...


class LocalSnapshotStore(SnapshotStore):
    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path(self, ref: str) -> Path:
        if ref.startswith("/") or ".." in Path(ref).parts or Path(ref).is_absolute():
            raise ValueError(f"invalid snapshot ref: {ref!r}")
        path = (self._root / ref).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"snapshot ref escapes storage root: {ref!r}")
        return path

    async def put(self, ref: str, data: bytes) -> None:
        path = self._path(ref)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, ref: str) -> bytes:
        return await asyncio.to_thread(self._path(ref).read_bytes)

    async def delete(self, ref: str) -> None:
        try:
            await asyncio.to_thread(self._path(ref).unlink)
        except FileNotFoundError:
            return

    async def exists(self, ref: str) -> bool:
        return await asyncio.to_thread(self._path(ref).exists)


@dataclass(frozen=True)
class S3StoreConfig:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"


class S3CompatibleSnapshotStore(SnapshotStore):
    """MinIO / S3 兼容快照存储。

    使用标准 S3 PUT/GET/DELETE/HEAD 对象接口；通过 botocore 可选依赖做 SigV4 签名。
    未安装 boto3 时回退到 httpx + AWS Signature V4 手写签名（仅这几个方法）。
    """

    def __init__(self, config: S3StoreConfig) -> None:
        self._config = config
        self._client: Any | None = None
        try:
            import boto3  # type: ignore

            self._client = boto3.client(
                "s3",
                endpoint_url=config.endpoint_url or None,
                aws_access_key_id=config.access_key,
                aws_secret_access_key=config.secret_key,
                region_name=config.region,
            )
        except ImportError:
            self._client = None

    def _url(self, ref: str) -> str:
        base = self._config.endpoint_url.rstrip("/")
        return f"{base}/{self._config.bucket}/{ref.lstrip('/')}"

    async def _request(self, method: str, ref: str, *, data: bytes | None = None) -> httpx.Response:
        if self._client is not None:
            return await asyncio.to_thread(self._boto_request, method, ref, data=data)
        return await self._signed_request(method, ref, data=data)

    def _boto_request(self, method: str, ref: str, *, data: bytes | None = None) -> httpx.Response:
        key = ref.lstrip("/")
        if method == "PUT":
            self._client.put_object(Bucket=self._config.bucket, Key=key, Body=data or b"")
            return httpx.Response(200)
        if method == "GET":
            response = self._client.get_object(Bucket=self._config.bucket, Key=key)
            return httpx.Response(200, content=response["Body"].read())
        if method == "DELETE":
            self._client.delete_object(Bucket=self._config.bucket, Key=key)
            return httpx.Response(204)
        if method == "HEAD":
            try:
                self._client.head_object(Bucket=self._config.bucket, Key=key)
                return httpx.Response(200)
            except Exception:
                return httpx.Response(404)
        raise ValueError(f"unsupported method: {method}")

    async def _signed_request(self, method: str, ref: str, *, data: bytes | None = None) -> httpx.Response:
        from agent_runtime.checkpoint.sigv4 import sign_s3_request

        headers, content = sign_s3_request(
            method=method,
            url=self._url(ref),
            access_key=self._config.access_key,
            secret_key=self._config.secret_key,
            region=self._config.region,
            payload=data or b"",
        )
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(method, self._url(ref), headers=headers, content=content)

    async def put(self, ref: str, data: bytes) -> None:
        response = await self._request("PUT", ref, data=data)
        response.raise_for_status()

    async def get(self, ref: str) -> bytes:
        response = await self._request("GET", ref)
        response.raise_for_status()
        return response.content

    async def delete(self, ref: str) -> None:
        response = await self._request("DELETE", ref)
        response.raise_for_status()

    async def exists(self, ref: str) -> bool:
        response = await self._request("HEAD", ref)
        return response.status_code == 200


def ensure_bucket(config: S3StoreConfig) -> None:
    """在应用启动时确保 bucket 存在；boto3 不可用时由部署侧预先创建。"""
    try:
        import boto3  # type: ignore
        from botocore.exceptions import ClientError  # type: ignore

        client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url or None,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region,
        )
        try:
            client.head_bucket(Bucket=config.bucket)
        except ClientError:
            client.create_bucket(Bucket=config.bucket)
    except ImportError:
        return


def build_snapshot_store(settings: Any) -> SnapshotStore:
    backend = settings.snapshot_storage_backend.strip().lower()
    if backend == "local":
        return LocalSnapshotStore(settings.snapshot_local_dir)
    if backend == "s3":
        if not all([settings.snapshot_s3_endpoint, settings.snapshot_s3_bucket, settings.snapshot_s3_access_key, settings.snapshot_s3_secret_key]):
            raise RuntimeError(
                "S3 snapshot storage requires SNAPSHOT_S3_ENDPOINT, SNAPSHOT_S3_BUCKET, "
                "SNAPSHOT_S3_ACCESS_KEY, SNAPSHOT_S3_SECRET_KEY"
            )
        config = S3StoreConfig(
            endpoint_url=settings.snapshot_s3_endpoint,
            bucket=settings.snapshot_s3_bucket,
            access_key=settings.snapshot_s3_access_key,
            secret_key=settings.snapshot_s3_secret_key,
            region=settings.snapshot_s3_region,
        )
        ensure_bucket(config)
        return S3CompatibleSnapshotStore(config)
    raise RuntimeError(f"unsupported SNAPSHOT_STORAGE_BACKEND: {backend!r}")
