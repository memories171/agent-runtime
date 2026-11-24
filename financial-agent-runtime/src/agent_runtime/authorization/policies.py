from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent_runtime.contracts import Principal


@dataclass
class AuthorizationRequest:
    principal: Principal
    action: str
    resource_type: str
    resource_id: str
    permission: str | None = None
    tenant_id: str | None = None
    owner_id: str | None = None
    sensitivity: str | None = None
    require_acl: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_tenant_id(self) -> str:
        return self.tenant_id or self.principal.tenant_id

    @property
    def effective_permissions(self) -> set[str]:
        return self.principal.permissions or self.principal.scopes

    @property
    def request_time(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class AuthorizationDecision:
    allowed: bool
    reason: str

    @classmethod
    def allow(cls) -> "AuthorizationDecision":
        return cls(allowed=True, reason="allowed")

    @classmethod
    def deny(cls, reason: str) -> "AuthorizationDecision":
        return cls(allowed=False, reason=reason)


def evaluate(request: AuthorizationRequest) -> AuthorizationDecision:
    if request.effective_tenant_id != request.principal.tenant_id:
        return AuthorizationDecision.deny("cross-tenant access is forbidden")

    permissions = request.effective_permissions
    if request.permission and request.permission not in permissions:
        return AuthorizationDecision.deny(f"permission required: {request.permission}")

    classification = request.context.get("resource_classification")
    if classification in {"confidential", "restricted"} and "data:sensitive" not in permissions:
        return AuthorizationDecision.deny(f"sensitive resource requires data:sensitive: {classification}")

    if request.owner_id and request.owner_id != request.principal.user_id:
        if request.action in {"write", "update", "delete"} and "resource:manage:tenant" not in permissions:
            return AuthorizationDecision.deny("resource owner mismatch and no tenant management permission")

    if request.context.get("approval_status") == "pending" and request.action == "approve":
        return AuthorizationDecision.deny("approval is still pending")

    return AuthorizationDecision.allow()
