from __future__ import annotations

import pytest
from fastapi import HTTPException

from agent_runtime import auth as auth_module
from agent_runtime.auth import get_principal
from agent_runtime.config import settings


@pytest.mark.asyncio
async def test_dev_header_allowed_only_in_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_mode", "dev")
    principal = await get_principal(
        authorization=None,
        x_tenant_id="tenant-a",
        x_user_id="user-a",
        x_scopes="run:create,memory:read",
    )
    assert principal.tenant_id == "tenant-a"
    assert principal.user_id == "user-a"
    assert "run:create" in principal.permissions
    assert principal.authentication_method == "dev"


@pytest.mark.asyncio
async def test_dev_header_rejected_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "dev")
    with pytest.raises(HTTPException) as exc:
        await get_principal(authorization=None, x_tenant_id="tenant-a", x_user_id="user-a", x_scopes=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_oidc_requires_bearer(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    with pytest.raises(HTTPException) as exc:
        await get_principal(authorization=None, x_tenant_id=None, x_user_id=None, x_scopes=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_oidc_invalid_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "oidc")

    async def bad_verify(token):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_module, "verify_jwt", bad_verify)
    with pytest.raises(HTTPException) as exc:
        await get_principal(
            authorization="Bearer invalid",
            x_tenant_id="tenant-a",
            x_user_id=None,
            x_scopes=None,
        )
    assert exc.value.status_code == 401
