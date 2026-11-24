from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_runtime.auth_jwt import verify_jwt
from agent_runtime.config import settings


def _key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", "https://issuer.example.com")
    monkeypatch.setattr(settings, "oidc_audience", "finora-api")
    monkeypatch.setattr(settings, "jwt_algorithms", "RS256,ES256")
    monkeypatch.setattr(settings, "jwt_leeway_seconds", 0)


@pytest.mark.asyncio
async def test_valid_token(monkeypatch):
    _configure(monkeypatch)
    private, public_pem = _key_pair()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user-a", "tenant_id": "tenant-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "exp": now + 3600, "nbf": now},
        private,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    claims = await verify_jwt(token, key_resolver=lambda kid, alg: public_pem)
    assert claims["sub"] == "user-a"
    assert claims["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_expired_token_rejected(monkeypatch):
    _configure(monkeypatch)
    private, public_pem = _key_pair()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now - 7200, "exp": now - 3600},
        private,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        await verify_jwt(token, key_resolver=lambda kid, alg: public_pem)


@pytest.mark.asyncio
async def test_invalid_signature_rejected(monkeypatch):
    _configure(monkeypatch)
    private, _ = _key_pair()
    _, other_public = _key_pair()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "exp": now + 3600},
        private,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(jwt.InvalidSignatureError):
        await verify_jwt(token, key_resolver=lambda kid, alg: other_public)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim,value", [("iss", "https://wrong.example.com"), ("aud", "wrong-audience")])
async def test_wrong_issuer_and_audience_rejected(monkeypatch, claim, value):
    _configure(monkeypatch)
    private, public_pem = _key_pair()
    now = int(time.time())
    payload = {"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "exp": now + 3600}
    payload[claim] = value
    token = jwt.encode(
        payload,
        private,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(jwt.InvalidIssuerError if claim == "iss" else jwt.InvalidAudienceError):
        await verify_jwt(token, key_resolver=lambda kid, alg: public_pem)


@pytest.mark.asyncio
async def test_not_before_rejected(monkeypatch):
    _configure(monkeypatch)
    private, public_pem = _key_pair()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "nbf": now + 600, "exp": now + 3600},
        private,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(jwt.ImmatureSignatureError):
        await verify_jwt(token, key_resolver=lambda kid, alg: public_pem)


@pytest.mark.asyncio
async def test_unsupported_algorithm_rejected(monkeypatch):
    _configure(monkeypatch)
    now = int(time.time())
    token = jwt.encode({"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "exp": now + 3600}, "secret", algorithm="HS256")
    with pytest.raises(ValueError, match="not allowed"):
        await verify_jwt(token, key_resolver=lambda kid, alg: "secret")


@pytest.mark.asyncio
async def test_jwks_key_rotation(monkeypatch):
    _configure(monkeypatch)
    old_private, old_public = _key_pair()
    new_private, new_public = _key_pair()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user-a", "iss": "https://issuer.example.com", "aud": "finora-api", "iat": now, "exp": now + 3600},
        new_private,
        algorithm="RS256",
        headers={"kid": "key-2"},
    )

    def resolver(kid, alg):
        return {"key-1": old_public, "key-2": new_public}[kid]

    claims = await verify_jwt(token, key_resolver=resolver)
    assert claims["sub"] == "user-a"
