from __future__ import annotations

from agent_runtime.authorization.policies import AuthorizationRequest
from agent_runtime.authorization.service import evaluate
from agent_runtime.contracts import Principal


def _principal(permissions: set[str], tenant_id: str = "tenant-a") -> Principal:
    return Principal(
        tenant_id=tenant_id,
        user_id="user-a",
        scopes=set(permissions),
        permissions=set(permissions),
        roles={"analyst"},
        authentication_method="oidc",
    )


def test_cross_tenant_default_deny():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"run:read"}),
        action="read",
        resource_type="agent_run",
        resource_id="run-1",
        tenant_id="tenant-b",
    ))
    assert decision.allowed is False
    assert decision.reason == "cross-tenant access is forbidden"


def test_missing_permission_denied():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"run:read"}),
        action="approve",
        resource_type="review_case",
        resource_id="case-1",
        permission="approval:approve",
    ))
    assert decision.allowed is False
    assert "approval:approve" in decision.reason


def test_sensitive_resource_requires_permission():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"run:read"}),
        action="read",
        resource_type="financial_transaction",
        resource_id="tx-1",
        context={"resource_classification": "restricted"},
    ))
    assert decision.allowed is False
    assert "data:sensitive" in decision.reason


def test_sensitive_resource_allowed_with_permission():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"run:read", "data:sensitive"}),
        action="read",
        resource_type="financial_transaction",
        resource_id="tx-1",
        context={"resource_classification": "restricted"},
    ))
    assert decision.allowed is True


def test_owner_mismatch_write_denied():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"memory:write"}),
        action="update",
        resource_type="memory",
        resource_id="mem-1",
        owner_id="user-b",
    ))
    assert decision.allowed is False


def test_pending_approval_denied():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"approval:approve"}),
        action="approve",
        resource_type="review_case",
        resource_id="case-1",
        context={"approval_status": "pending"},
    ))
    assert decision.allowed is False


def test_default_allow_after_checks():
    decision = evaluate(AuthorizationRequest(
        principal=_principal({"run:read"}),
        action="read",
        resource_type="agent_run",
        resource_id="run-1",
        owner_id="user-a",
    ))
    assert decision.allowed is True
