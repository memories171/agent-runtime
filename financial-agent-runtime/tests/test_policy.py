import pytest

from agent_runtime.contracts import Principal
from agent_runtime.tools.gateway import ApprovalRequired, ToolDenied, authorize_tool
from agent_runtime.tools.registry import registry


def test_tool_allows_same_tenant_with_required_scope():
    tool = registry.get("demo.echo")
    principal = Principal(tenant_id="t1", user_id="u1", scopes={"agent:execute"})
    authorize_tool(principal.model_dump(), "t1", tool)


def test_tool_denies_cross_tenant():
    tool = registry.get("demo.echo")
    principal = Principal(tenant_id="t1", user_id="u1", scopes={"agent:execute"})
    with pytest.raises(ToolDenied):
        authorize_tool(principal.model_dump(), "t2", tool)


def test_high_risk_tool_requires_approval():
    tool = registry.get("demo.notify")
    principal = Principal(tenant_id="t1", user_id="u1", scopes={"agent:execute", "external:send"})
    with pytest.raises(ApprovalRequired):
        authorize_tool(principal.model_dump(), "t1", tool)
    authorize_tool(principal.model_dump(), "t1", tool, approval_granted=True)


@pytest.mark.asyncio
async def test_tool_gateway_validates_json_schema():
    from agent_runtime.tools.gateway import execute_tool

    principal = {"tenant_id": "t1", "user_id": "u1", "scopes": ["agent:execute"]}
    with pytest.raises(ValueError, match="invalid arguments"):
        await execute_tool("demo.add", {"a": "not-a-number", "b": 2}, principal, "t1")


def test_run_visibility_is_owner_or_tenant_reader():
    from agent_runtime.auth import can_access_run

    from_owner = Principal(tenant_id="t1", user_id="u1", scopes=set())
    peer = Principal(tenant_id="t1", user_id="u2", scopes=set())
    manager = Principal(tenant_id="t1", user_id="u2", scopes={"runs:read:tenant"})
    assert can_access_run(from_owner, "u1")
    assert not can_access_run(peer, "u1")
    assert can_access_run(manager, "u1")


def test_idempotency_key_is_stable():
    from agent_runtime.tools.gateway import make_idempotency_key

    first = make_idempotency_key("run-1", 3, "demo.add", {"b": 2, "a": 1})
    second = make_idempotency_key("run-1", 3, "demo.add", {"a": 1, "b": 2})
    assert first == second


def test_run_management_requires_owner_or_explicit_manage_scope():
    from agent_runtime.auth import can_manage_run

    owner = Principal(tenant_id="t1", user_id="u1", scopes=set())
    peer_reader = Principal(tenant_id="t1", user_id="u2", scopes={"runs:read:tenant"})
    manager = Principal(tenant_id="t1", user_id="u2", scopes={"runs:manage:tenant"})
    assert can_manage_run(owner, "u1")
    assert not can_manage_run(peer_reader, "u1")
    assert can_manage_run(manager, "u1")
