from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, Awaitable, Callable

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from agent_runtime.config import settings

KeyResolver = Callable[[str | None, str], Any | Awaitable[Any]]


def _jwk_to_key(jwk: dict[str, Any]) -> Any:
    kty = jwk.get("kty")
    alg = jwk.get("alg", "")
    payload = json.dumps(jwk)
    if kty == "RSA" or alg.startswith("RS"):
        return RSAAlgorithm.from_jwk(payload)
    if kty == "EC" or alg.startswith("ES"):
        return ECAlgorithm.from_jwk(payload)
    raise ValueError(f"unsupported JWK key type: {kty}/{alg}")


class JWKSClient:
    def __init__(self, url: str, cache_ttl_seconds: int):
        self.url = url
        self.cache_ttl_seconds = cache_ttl_seconds
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            document = response.json()
        self._keys = {
            item["kid"]: item
            for item in document.get("keys", [])
            if isinstance(item, dict) and item.get("kid")
        }
        self._fetched_at = time.monotonic()

    async def get_signing_key(self, kid: str | None, alg: str) -> Any:
        async with self._lock:
            stale = time.monotonic() - self._fetched_at > self.cache_ttl_seconds
            if not self._keys or stale:
                await self._fetch()
            jwk = self._keys.get(kid or "")
            if jwk is None:
                await self._fetch()
                jwk = self._keys.get(kid or "")
        if jwk is None:
            raise ValueError(f"JWKS signing key not found for kid={kid!r}")
        return _jwk_to_key(jwk)


_jwks_client: JWKSClient | None = None


def get_jwks_client() -> JWKSClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = JWKSClient(settings.oidc_jwks_url, settings.jwks_cache_ttl_seconds)
    return _jwks_client


async def verify_jwt(token: str, key_resolver: KeyResolver | None = None) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm not in settings.jwt_algorithm_list:
        raise ValueError(f"JWT algorithm not allowed: {algorithm!r}")
    kid = header.get("kid")
    resolver = key_resolver or get_jwks_client().get_signing_key
    key = resolver(kid, algorithm)
    if inspect.isawaitable(key):
        key = await key
    return jwt.decode(
        token,
        key=key,
        algorithms=[algorithm],
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        leeway=settings.jwt_leeway_seconds,
        options={"require": ["exp"]},
    )
