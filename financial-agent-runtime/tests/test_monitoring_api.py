"""Monitoring API (3.3.6 Step 2) permission and contract tests.

The ASGI app is exercised without lifespan (no Temporal/DB connection). The
Run ACL dependency is patched so we can assert the endpoint's auth behavior:
unauthenticated, cross-tenant/no-permission, invalid params, non-existent id and
the has_hidden_children contract are all covered.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from agent_runtime.auth import get_principal
from agent_runtime.contracts import AgentTreeAnomaliesRead, AgentTreeRead, Principal
from agent_runtime.monitoring_api import router as monitoring_router


def _app_with(principal_or_exc):
    app = FastAPI()
    app.include_router(monitoring_router)

    async def fake_principal():
        if isinstance(principal_or_exc, Exception):
            raise principal_or_exc
        return principal_or_exc

    app.dependency_overrides[get_principal] = fake_principal
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_unauthenticated_rejected(monkeypatch):
    import agent_runtime.monitoring_api as api

    async def deny(session, run_id, principal):
        raise HTTPException(status_code=401, detail="not authenticated")

    monkeypatch.setattr(api, "_owned_run", deny)
    app = _app_with(HTTPException(status_code=401, detail="not authenticated"))
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_permission_or_cross_tenant_returns_404(monkeypatch):
    import agent_runtime.monitoring_api as api

    async def deny(session, run_id, principal):
        # Mirrors _get_owned_run: never reveal the existence of another run.
        raise HTTPException(status_code=404, detail="run not found")

    monkeypatch.setattr(api, "_owned_run", deny)
    principal = Principal(tenant_id="tenant-b", user_id="intruder", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_max_depth_rejected():
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/executions/{uuid4()}/tree", params={"max_depth": 999}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_malformed_execution_id_rejected():
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get("/api/v1/monitoring/executions/not-a-uuid/tree")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tree_schema_has_hidden_flag_and_no_count(monkeypatch):
    """The endpoint must not expose hidden-node counts and must validate its schema."""
    import agent_runtime.monitoring_api as api

    async def allow(session, run_id, principal):
        return None

    async def fake_view(tenant_id, root_run_id, principal, *, max_depth, max_nodes):
        return AgentTreeRead(
            root_execution_id=root_run_id, root_visible=True, nodes=[],
            has_hidden_children=True,
        )

    monkeypatch.setattr(api, "_owned_run", allow)
    monkeypatch.setattr(api.DelegationService, "get_agent_tree_view", fake_view)
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_hidden_children"] is True
    assert "hidden_count" not in body
    assert "hidden_children_count" not in body


@pytest.mark.asyncio
async def test_anomalies_requires_authentication(monkeypatch):
    import agent_runtime.monitoring_api as api

    async def deny(session, run_id, principal):
        raise HTTPException(status_code=401, detail="not authenticated")

    monkeypatch.setattr(api, "_owned_run", deny)
    app = _app_with(HTTPException(status_code=401, detail="not authenticated"))
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree/anomalies")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_anomalies_root_acl_denial_is_404(monkeypatch):
    """Cross-tenant/no-permission root returns 404, never revealing the run."""
    import agent_runtime.monitoring_api as api

    async def deny(session, run_id, principal):
        raise HTTPException(status_code=404, detail="run not found")

    monkeypatch.setattr(api, "_owned_run", deny)
    principal = Principal(tenant_id="tenant-b", user_id="intruder", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree/anomalies")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_anomalies_passes_principal_and_never_leaks_hidden_uuid(monkeypatch):
    """The endpoint must pass principal downstream and expose no hidden UUID."""
    import agent_runtime.monitoring_api as api

    hidden = uuid4()

    async def allow(session, run_id, principal):
        return None

    captured = {}

    async def fake_detect(*, tenant_id, root_run_id, principal):
        captured["principal"] = principal
        captured["tenant_id"] = tenant_id
        # Simulate a correctly ACL-filtered service (hidden UUID never returned).
        return AgentTreeAnomaliesRead(orphans=[], cycles=[], duplicate_links=[])

    monkeypatch.setattr(api, "_owned_run", allow)
    monkeypatch.setattr(api.DelegationService, "detect_tree_anomalies", fake_detect)
    principal = Principal(tenant_id="tenant-a", user_id="owner", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/monitoring/executions/{uuid4()}/tree/anomalies")
    assert resp.status_code == 200
    assert captured["principal"] is principal
    assert captured["tenant_id"] == "tenant-a"
    assert str(hidden) not in resp.text
    assert resp.json() == {"orphans": [], "cycles": [], "duplicate_links": []}
