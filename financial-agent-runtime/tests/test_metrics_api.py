"""Metrics endpoint API contract and ACL tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from agent_runtime.auth import get_principal
from agent_runtime.contracts import ExecutionMetricsRead, Principal
from agent_runtime.db import get_session
from agent_runtime.monitoring_api import router as monitoring_router

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=30)


def _app_with(principal_or_exc):
    app = FastAPI()
    app.include_router(monitoring_router)

    async def fake_principal():
        if isinstance(principal_or_exc, Exception):
            raise principal_or_exc
        return principal_or_exc

    app.dependency_overrides[get_principal] = fake_principal

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _params(start=START, end=END):
    return {"start_time": start.isoformat(), "end_time": end.isoformat()}


def _stub_metrics(**overrides):
    payload = {
        "current_parallelism": 2,
        "max_parallelism": 3,
        "average_parallelism": 1.5,
        "total_agent_active_time": 45.0,
        "delegation_attempt_count": 4,
        "distinct_child_run_count": 3,
        "distinct_node_count": 2,
        "unknown_interval_count": 0,
        "partial_interval_count": 1,
        "data_quality": "OK",
        "unavailable_reasons": [],
    }
    payload.update(overrides)
    return ExecutionMetricsRead(**payload)


def _patch(monkeypatch, *, owned=None, metrics=None):
    import agent_runtime.monitoring_api as api

    async def allow(session, run_id, principal):
        return None

    if owned is not None:
        allow = owned

    monkeypatch.setattr(api, "_owned_run", allow)
    monkeypatch.setattr(api.ExecutionMetricsService, "collect", metrics or _async_metrics())


def _async_metrics(result: ExecutionMetricsRead | None = None):
    result = result or _stub_metrics()

    async def collect(**kwargs):
        return result

    return collect


@pytest.mark.asyncio
async def test_metrics_requires_authentication():
    principal_exc = HTTPException(status_code=401, detail="not authenticated")
    app = _app_with(principal_exc)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics", params=_params()
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_metrics_root_acl_denial_is_404(monkeypatch):
    async def deny(session, run_id, principal):
        raise HTTPException(status_code=404, detail="run not found")

    _patch(monkeypatch, owned=deny)
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics", params=_params()
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_metrics_rejects_invalid_uuid_and_window():
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        bad_uuid = await client.get(
            "/api/v1/monitoring/runs/not-a-uuid/metrics", params=_params()
        )
        bad_window = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics",
            params=_params(start=END, end=START),
        )
        naive = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics",
            params=_params(start=START.replace(tzinfo=None)),
        )
    assert bad_uuid.status_code == 422
    assert bad_window.status_code == 400
    assert naive.status_code == 400


@pytest.mark.asyncio
async def test_metrics_ok_response_contract(monkeypatch):
    _patch(monkeypatch)
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics", params=_params()
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "delegated_agents"
    assert body["data_quality"] == "OK"
    assert "total_agent_execution_time" not in body
    assert "parallel_efficiency" not in body
    assert "active_parallel_efficiency" not in body
    assert "hidden" not in str(body).lower()


@pytest.mark.asyncio
async def test_metrics_degraded_and_unavailable_reasons_are_generic(monkeypatch):
    degraded = _stub_metrics(
        data_quality="DEGRADED",
        unavailable_reasons=["ROOT_MISMATCH", "UNKNOWN_INTERVALS"],
    )
    _patch(monkeypatch, metrics=_async_metrics(degraded))
    principal = Principal(tenant_id="t", user_id="u", authentication_method="dev")
    app = _app_with(principal)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics", params=_params()
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body["data_quality"] == "DEGRADED"
    assert set(body["unavailable_reasons"]) == {"ROOT_MISMATCH", "UNKNOWN_INTERVALS"}
    assert all(item.upper() == item for item in body["unavailable_reasons"])
