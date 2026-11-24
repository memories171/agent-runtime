"""Task 5: security regression suite (7 scenarios).

Each test is a negative case asserting that an unauthorized caller is denied
and that no protected data leaks. Covers:

1. unauthorized user reading a child run  (see test_agent_tree_postgres.py P1-2)
2. cross-tenant data read                 (test_cross_tenant_memory_query_scope)
3. forged JWT permissions claim          (see test_jwt_claim_boundary.py)
4. unauthorized admin API call
5. unauthorized metrics view
6. unauthorized memory access
7. unauthorized MCP tool call
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from agent_runtime.auth import get_principal, require_permission
from agent_runtime.contracts import Principal
from agent_runtime.monitoring_api import router as monitoring_router
from agent_runtime.tools.gateway import ApprovalRequired, ToolDenied, authorize_tool
from agent_runtime.tools.registry import registry


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _low_priv(tenant="tenant-a", user="user-a") -> Principal:
    return Principal(
        tenant_id=tenant,
        user_id=user,
        scopes={"agent:execute"},
        permissions={"agent:execute"},
        authentication_method="dev",
    )


# ---------------------------------------------------------------------------
# Scenario 4: unauthorized admin API call
# ---------------------------------------------------------------------------
def test_admin_api_denied_without_manage_permission():
    """A low-privilege caller cannot pass the admin endpoint's permission gate."""
    with pytest.raises(HTTPException) as exc:
        require_permission(_low_priv(), "user:manage")
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        require_permission(_low_priv(), "audit:read")
    assert exc.value.status_code == 403


def test_admin_api_allows_explicit_permission():
    admin = Principal(
        tenant_id="tenant-a", user_id="admin",
        scopes={"user:manage", "audit:read"},
        permissions={"user:manage", "audit:read"},
        authentication_method="dev",
    )
    require_permission(admin, "user:manage")
    require_permission(admin, "audit:read")


@pytest.mark.asyncio
async def test_security_snapshot_denied_without_scope():
    """platform security snapshot requires an explicit security scope."""
    from agent_runtime.db import get_session
    from agent_runtime.platform_api import get_security_snapshot

    async def fake_session():
        yield object()

    app = FastAPI()
    app.add_api_route("/security/snapshot", get_security_snapshot, methods=["GET"])
    app.dependency_overrides[get_principal] = lambda: _low_priv()
    app.dependency_overrides[get_session] = fake_session

    async with _client(app) as client:
        resp = await client.get("/security/snapshot")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scenario 5: unauthorized metrics view
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_metrics_cross_tenant_root_denied(monkeypatch):
    """Cross-tenant/no-permission root returns 404, never metrics."""
    import agent_runtime.monitoring_api as api
    from agent_runtime.db import get_session

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=30)

    async def deny(session, run_id, principal):
        raise HTTPException(status_code=404, detail="run not found")

    async def fake_session():
        yield object()

    monkeypatch.setattr(api, "_owned_run", deny)
    app = FastAPI()
    app.include_router(monitoring_router)
    app.dependency_overrides[get_principal] = lambda: _low_priv("tenant-b", "intruder")
    app.dependency_overrides[get_session] = fake_session
    async with _client(app) as client:
        resp = await client.get(
            f"/api/v1/monitoring/runs/{uuid4()}/metrics",
            params={"start_time": start.isoformat(), "end_time": end.isoformat()},
        )
    assert resp.status_code == 404
    assert "delegation_attempt_count" not in resp.text


# ---------------------------------------------------------------------------
# Scenario 6: unauthorized memory access
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_memory_read_requires_scope():
    """GET /memory/search requires memory:read; a low-priv caller is denied."""
    from agent_runtime.api import memory_search

    with pytest.raises(HTTPException) as exc:
        await memory_search(q="secret", principal=_low_priv(), session=object())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_memory_write_requires_scope():
    from agent_runtime.api import add_memory
    from agent_runtime.contracts import MemoryCreate

    body = MemoryCreate(content="secret", scope="user", memory_type="semantic")
    with pytest.raises(HTTPException) as exc:
        await add_memory(body=body, principal=_low_priv(), session=object())
    assert exc.value.status_code == 403


def test_memory_visible_conditions_hide_other_users_private_scope():
    """user-scoped memory is constrained to the caller's own owner_id."""
    from agent_runtime.memory.service import _visible_conditions

    principal = _low_priv()
    cond = _visible_conditions(principal)
    compiled = str(cond.compile(compile_kwargs={"literal_binds": True}))
    assert "agent_memories.owner_id" in compiled
    assert "user-a" in compiled
    # No team/organization escalation without explicit read scopes.
    assert "team" not in compiled.lower()
    assert "organization" not in compiled.lower()


def test_memory_visible_conditions_require_scope_for_team_org():
    """team/organization scopes only become visible with explicit permissions."""
    from agent_runtime.memory.service import _visible_conditions

    principal = Principal(
        tenant_id="tenant-a", user_id="user-a",
        scopes={"memory:read:team"}, permissions={"memory:read:team"},
        authentication_method="dev",
    )
    compiled = str(_visible_conditions(principal).compile(
        compile_kwargs={"literal_binds": True}
    ))
    assert "team" in compiled.lower()
    assert "organization" not in compiled.lower()


# ---------------------------------------------------------------------------
# Scenario 7: unauthorized MCP tool call
# ---------------------------------------------------------------------------
def test_mcp_tool_denied_cross_tenant():
    """Calling a tool with a mismatched tenant is denied."""
    tool = registry.get("demo.echo")
    principal = _low_priv("tenant-a", "user-a")
    with pytest.raises(ToolDenied):
        authorize_tool(principal.model_dump(), "tenant-b", tool)


def test_mcp_tool_denied_missing_scope():
    """Calling a tool without its required scope is denied."""
    tool = registry.get("demo.echo")
    principal = Principal(
        tenant_id="tenant-a", user_id="user-a",
        scopes=set(), permissions=set(), authentication_method="dev",
    )
    with pytest.raises(ToolDenied):
        authorize_tool(principal.model_dump(), "tenant-a", tool)


def test_high_risk_mcp_tool_requires_approval():
    """A high-risk tool cannot be called without approval."""
    tool = registry.get("demo.notify")
    principal = Principal(
        tenant_id="tenant-a", user_id="user-a",
        scopes={"agent:execute", "external:send"},
        permissions={"agent:execute", "external:send"},
        authentication_method="dev",
    )
    with pytest.raises(ApprovalRequired):
        authorize_tool(principal.model_dump(), "tenant-a", tool, approval_granted=False)
