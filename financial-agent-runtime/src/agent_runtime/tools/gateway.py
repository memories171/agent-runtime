from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_runtime.authorization.policies import AuthorizationRequest, evaluate
from agent_runtime.contracts import Principal
from agent_runtime.tools.registry import ToolDefinition, registry


class ToolDenied(Exception):
    pass


class ApprovalRequired(Exception):
    def __init__(self, tool_name: str, arguments: dict[str, Any]):
        self.tool_name = tool_name
        self.arguments = arguments
        super().__init__(f"Approval required for {tool_name}")


def authorize_tool(
    principal_data: dict[str, Any],
    tenant_id: str,
    tool: ToolDefinition,
    approval_granted: bool = False,
) -> None:
    principal = Principal.model_validate(principal_data)
    if principal.tenant_id != tenant_id:
        raise ToolDenied("cross-tenant execution is forbidden")
    if not tool.required_scopes.issubset(principal.scopes):
        raise ToolDenied("required tool scope is missing")
    if tool.risk_level in {"high", "critical"} and not approval_granted:
        raise ApprovalRequired(tool.name, {})
    decision = evaluate(
        AuthorizationRequest(
            principal=principal,
            action="execute",
            resource_type="tool",
            resource_id=tool.name,
            tenant_id=tenant_id,
            context={"risk_level": tool.risk_level, "approval_granted": approval_granted},
        )
    )
    if not decision.allowed:
        raise ToolDenied(decision.reason)


def make_idempotency_key(run_id: str, step_number: int, tool_name: str, args: dict) -> str:
    stable = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raw = f"{run_id}:{step_number}:{tool_name}:{stable}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    principal_data: dict[str, Any],
    tenant_id: str,
    approval_granted: bool = False,
    run_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    tool = registry.get(tool_name)
    if tool is None:
        raise ToolDenied(f"tool is not registered: {tool_name}")
    authorize_tool(principal_data, tenant_id, tool, approval_granted)
    from jsonschema import ValidationError, validate
    try:
        validate(instance=arguments, schema=tool.input_schema)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {tool_name}: {exc.message}") from exc
    if tool.handler is not None:
        if tool.context_aware:
            context = Principal.model_validate(principal_data).model_dump()
            context["run_id"] = run_id
            context["idempotency_key"] = idempotency_key
            return await tool.handler(arguments, context)
        return await tool.handler(arguments)
    if tool.mcp_server_url and tool.mcp_tool_name:
        from agent_runtime.tools.mcp_client import MCPToolClient
        return await MCPToolClient().call_tool(tool.mcp_server_url, tool.mcp_tool_name, arguments)
    raise RuntimeError(f"tool has no execution adapter: {tool_name}")


def attenuated_principal_data(principal_data: dict[str, Any], capabilities: set[str]) -> dict[str, Any]:
    """用 Token 能力集覆盖 principal 的权限，不让原始宽权限穿透到子 Agent。"""
    principal = Principal.model_validate(principal_data)
    narrowed = principal.model_copy(update={"permissions": set(capabilities), "scopes": set(capabilities)})
    return narrowed.model_dump(mode="json")


async def authorize_tool_with_capability(
    principal_data: dict[str, Any],
    tenant_id: str,
    tool: ToolDefinition,
    capability_token_id: str,
    *,
    approval_granted: bool = False,
    resource_scope: dict[str, Any] | None = None,
    subject_run_id: str | None = None,
):
    """先实时校验 Capability Token，再执行既有 Tool Gateway 授权链。

    capability_token_id 只是定位符，不是凭证；Token 状态始终从数据库读取。
    """
    from agent_runtime.orchestration.token_service import CapabilityDenied, CapabilityTokenService

    try:
        actor = Principal.model_validate(principal_data)
    except Exception as exc:
        raise CapabilityDenied("invalid_principal", str(exc)) from exc

    token = await CapabilityTokenService.validate(
        capability_token_id,
        tenant_id,
        required_capabilities=set(tool.required_scopes),
        resource_scope=resource_scope,
        subject_run_id=subject_run_id,
        actor=actor,
    )
    authorize_tool(
        attenuated_principal_data(principal_data, set(token.capabilities)),
        tenant_id,
        tool,
        approval_granted=approval_granted,
    )
    return token
