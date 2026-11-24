"""P2-9 / Task 4: JWT authorization claim trust-boundary regression tests.

A validly signed token must not self-declare elevated permissions or roles.
The DB-resolved identity is the only authoritative source of authorization.
"""
from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_runtime import auth as auth_module
from agent_runtime.config import settings
from agent_runtime.contracts import Principal


def _key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", "https://issuer.example.com")
    monkeypatch.setattr(settings, "oidc_audience", "finora-api")
    monkeypatch.setattr(settings, "jwt_algorithms", "RS256,ES256")
    monkeypatch.setattr(settings, "jwt_leeway_seconds", 0)
    monkeypatch.setattr(settings, "trust_jwt_authorization_claims", False)


def _db_identity(tenant="tenant-a", user="user-a"):
    """Simulate a low-privilege identity resolved from the DB."""
    return Principal(
        tenant_id=tenant,
        user_id=user,
        scopes={"memory:read"},
        permissions={"memory:read"},
        roles={"viewer"},
        authentication_method="oidc",
        token_subject=user,
    )


def _token(claims, private):
    now = int(time.time())
    payload = {
        "sub": "user-a",
        "tenant_id": "tenant-a",
        "iss": "https://issuer.example.com",
        "aud": "finora-api",
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(claims)
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": "key-1"})


async def _principal_from_token(token: str) -> Principal:
    """Call get_principal with explicit header args (FastAPI Header defaults
    must be overridden when calling the dependency function directly)."""
    return await auth_module.get_principal(
        authorization=f"Bearer {token}",
        x_tenant_id=None,
        x_user_id=None,
        x_scopes=None,
    )


def _patch_infra(monkeypatch):
    """Make get_principal hermetic: no RLS context or DB audit writes."""
    monkeypatch.setattr(auth_module, "set_current_tenant", lambda tenant: None)

    from agent_runtime.authorization import audit as audit_module

    async def noop_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(audit_module, "record_auth_event", noop_audit)


@pytest.mark.asyncio
async def test_forged_permissions_claim_is_not_adopted(monkeypatch):
    """A signed token claiming runs:read:tenant must not grant it."""
    _configure(monkeypatch)
    private, public_pem = _key_pair()

    async def fake_verify(token, key_resolver=None):
        return jwt.decode(
            token, public_pem, algorithms=["RS256"],
            audience="finora-api", issuer="https://issuer.example.com",
        )

    async def fake_load_identity(user_id, tenant_id):
        return _db_identity(tenant_id, user_id)

    monkeypatch.setattr(auth_module, "verify_jwt", fake_verify)
    monkeypatch.setattr(auth_module, "_load_identity", fake_load_identity)
    _patch_infra(monkeypatch)

    token = _token({
        "permissions": ["runs:read:tenant", "platform:manage", "memory:write"],
        "roles": ["admin"],
    }, private)
    principal = await _principal_from_token(token)

    assert "runs:read:tenant" not in principal.permissions
    assert "platform:manage" not in principal.permissions
    assert "memory:write" not in principal.permissions
    assert "admin" not in principal.roles
    assert principal.permissions == {"memory:read"}
    assert principal.scopes == {"memory:read"}  # scopes not amplified


@pytest.mark.asyncio
async def test_expired_token_rejected_by_get_principal(monkeypatch):
    """Expired tokens are rejected before any authorization is computed."""
    from fastapi import HTTPException

    _configure(monkeypatch)
    private, public_pem = _key_pair()

    async def fake_verify(token, key_resolver=None):
        return jwt.decode(
            token, public_pem, algorithms=["RS256"],
            audience="finora-api", issuer="https://issuer.example.com",
        )

    async def fake_load_identity(user_id, tenant_id):
        return _db_identity(tenant_id, user_id)

    monkeypatch.setattr(auth_module, "verify_jwt", fake_verify)
    monkeypatch.setattr(auth_module, "_load_identity", fake_load_identity)
    _patch_infra(monkeypatch)

    now = int(time.time())
    expired = jwt.encode({
        "sub": "user-a", "tenant_id": "tenant-a",
        "iss": "https://issuer.example.com", "aud": "finora-api",
        "iat": now - 7200, "exp": now - 3600,
        "permissions": ["runs:read:tenant"],
    }, private, algorithm="RS256", headers={"kid": "key-1"})

    with pytest.raises(HTTPException) as exc:
        await _principal_from_token(expired)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_and_audience_rejected(monkeypatch):
    """Token validation enforces issuer and audience."""
    from fastapi import HTTPException

    _configure(monkeypatch)
    private, public_pem = _key_pair()

    async def fake_verify(token, key_resolver=None):
        return jwt.decode(
            token, public_pem, algorithms=["RS256"],
            audience="finora-api", issuer="https://issuer.example.com",
        )

    async def fake_load_identity(user_id, tenant_id):
        return _db_identity(tenant_id, user_id)

    monkeypatch.setattr(auth_module, "verify_jwt", fake_verify)
    monkeypatch.setattr(auth_module, "_load_identity", fake_load_identity)
    _patch_infra(monkeypatch)

    for claim, value in (("iss", "https://evil.example.com"), ("aud", "other-api")):
        token = _token({claim: value}, private)
        with pytest.raises(HTTPException) as exc:
            await _principal_from_token(token)
        assert exc.value.status_code == 401


def test_production_forbids_trusting_jwt_authorization_claims(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", "https://issuer.example.com")
    monkeypatch.setattr(settings, "oidc_audience", "finora-api")
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://issuer.example.com/jwks")
    monkeypatch.setattr(settings, "trust_jwt_authorization_claims", True)
    with pytest.raises(RuntimeError, match="TRUST_JWT_AUTHORIZATION_CLAIMS"):
        settings.validate_runtime_config()


@pytest.mark.asyncio
async def test_capability_token_not_escalated_by_forged_claim(monkeypatch):
    """Capability root inherits only DB permissions, never token claims."""
    from agent_runtime.orchestration.token_service import CapabilityTokenService

    _configure(monkeypatch)
    private, public_pem = _key_pair()

    async def fake_verify(token, key_resolver=None):
        return jwt.decode(
            token, public_pem, algorithms=["RS256"],
            audience="finora-api", issuer="https://issuer.example.com",
        )

    async def fake_load_identity(user_id, tenant_id):
        return _db_identity(tenant_id, user_id)

    monkeypatch.setattr(auth_module, "verify_jwt", fake_verify)
    monkeypatch.setattr(auth_module, "_load_identity", fake_load_identity)
    _patch_infra(monkeypatch)

    token = _token({"permissions": ["platform:manage", "runs:read:tenant"]}, private)
    principal = await _principal_from_token(token)

    # Simulate issuing a capability token would only see the DB permissions.
    from agent_runtime.orchestration.token_service import _principal_permissions
    caps = _principal_permissions(principal)
    assert "platform:manage" not in caps
    assert "runs:read:tenant" not in caps
