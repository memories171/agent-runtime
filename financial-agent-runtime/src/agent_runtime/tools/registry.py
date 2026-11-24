from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from agent_runtime.rls import tenant_session


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    required_scopes: frozenset[str]
    display_name: str | None = None
    risk_level: str = "low"
    idempotent: bool = True
    handler: Callable[..., Awaitable[dict[str, Any]]] | None = None
    mcp_server_url: str | None = None
    mcp_tool_name: str | None = None
    context_aware: bool = False
    requires_distinct_approver: bool = False


async def _echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args.get("text", "")}


async def _add(args: dict[str, Any]) -> dict[str, Any]:
    return {"sum": float(args["a"]) + float(args["b"])}


async def _notify(args: dict[str, Any]) -> dict[str, Any]:
    # Demo-only side effect. Replace with a real idempotent provider adapter.
    return {"accepted": True, "recipient": args["recipient"], "message": args["message"]}


def _audit_record(FinancialAuditLog, context: dict[str, Any], action: str,
                  resource_type: str, resource_id: str, details: dict[str, Any] | None = None):
    from uuid import UUID
    return FinancialAuditLog(
        tenant_id=context["tenant_id"], actor_user_id=context["user_id"],
        run_id=UUID(context["run_id"]) if context.get("run_id") else None,
        action=action, resource_type=resource_type, resource_id=resource_id,
        details=details or {},
    )


async def _require_resource_grant(session, context: dict[str, Any], resource_type: str,
                                  resource_id: str, permission: str) -> None:
    from sqlalchemy import select
    from agent_runtime.db import FinancialResourceGrant

    grant = (await session.execute(select(FinancialResourceGrant.grant_id).where(
        FinancialResourceGrant.tenant_id == context["tenant_id"],
        FinancialResourceGrant.user_id == context["user_id"],
        FinancialResourceGrant.resource_type.in_([resource_type, "*"]),
        FinancialResourceGrant.resource_id.in_([resource_id, "*"]),
        FinancialResourceGrant.permission.in_([permission, "*"]),
    ).limit(1))).scalar_one_or_none()
    if grant is None:
        raise PermissionError(f"user has no {permission} grant for {resource_type}:{resource_id}")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.register(
            ToolDefinition(
                name="demo.echo",
                display_name="文本回显",
                description="回显输入文本。安全的只读演示工具，用于验证工具调用链路。",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                required_scopes=frozenset({"agent:execute"}),
                handler=_echo,
            )
        )
        self.register(
            ToolDefinition(
                name="demo.add",
                display_name="数值求和",
                description="计算两个数值的和。仅用于演示工具参数校验与执行。",
                input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
                required_scopes=frozenset({"agent:execute"}),
                handler=_add,
            )
        )
        self.register(
            ToolDefinition(
                name="demo.notify",
                display_name="外部通知模拟",
                description="模拟发送外部通知。属于高风险动作，必须取得独立审批后才能调用。",
                input_schema={"type": "object", "properties": {"recipient": {"type": "string"}, "message": {"type": "string"}}, "required": ["recipient", "message"]},
                required_scopes=frozenset({"agent:execute", "external:send"}),
                risk_level="high",
                idempotent=True,
                handler=_notify,
            )
        )
        self._register_financial_tools()

    def _register_financial_tools(self) -> None:
        common_object = {"type": "object", "additionalProperties": False}
        self.register(ToolDefinition(
            name="financial.get_customer_profile",
            display_name="客户资料查询",
            description="读取当前租户授权范围内的客户基本资料、KYC 状态和风险评级；仅限必要字段。",
            input_schema={**common_object, "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
            required_scopes=frozenset({"agent:execute", "financial:customer:read"}),
            handler=_financial_get_customer, context_aware=True,
        ))
        self.register(ToolDefinition(
            name="financial.get_account_summary",
            display_name="账户摘要查询",
            description="读取当前租户账户摘要、掩码账号、币种、余额和状态。",
            input_schema={**common_object, "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
            required_scopes=frozenset({"agent:execute", "financial:account:read"}),
            handler=_financial_get_account, context_aware=True,
        ))
        self.register(ToolDefinition(
            name="financial.list_transactions",
            display_name="交易明细查询",
            description="查询当前租户账户交易明细，支持可选起止时间和结果条数。金额以最小货币单位整数返回。",
            input_schema={**common_object, "properties": {
                "account_id": {"type": "string"}, "start_at": {"type": "string"},
                "end_at": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}
            }, "required": ["account_id"]},
            required_scopes=frozenset({"agent:execute", "financial:transactions:read"}),
            handler=_financial_list_transactions, context_aware=True,
        ))
        self.register(ToolDefinition(
            name="financial.screen_transactions",
            display_name="交易监测筛查",
            description="对授权账户交易运行可解释的演示复核规则，返回重复参考号和短时借记频次提示；不作 AML 法律认定。",
            input_schema={**common_object, "properties": {
                "account_id": {"type": "string"}, "velocity_count": {"type": "integer", "minimum": 2, "maximum": 20},
                "velocity_window_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                "lookback_days": {"type": "integer", "minimum": 1, "maximum": 365}
            }, "required": ["account_id"]},
            required_scopes=frozenset({"agent:execute", "financial:transactions:read", "financial:monitoring:run"}),
            handler=_financial_screen_transactions, context_aware=True,
        ))
        self.register(ToolDefinition(
            name="financial.create_review_case",
            display_name="复核案件创建",
            description="创建内部 KYC/AML/交易复核案件。必须经独立审批人批准；不会冻结账户、提交监管报告或执行资金操作。",
            input_schema={**common_object, "properties": {
                "case_type": {"type": "string", "enum": ["KYC", "AML", "TRANSACTION_REVIEW"]},
                "subject_id": {"type": "string"}, "rationale": {"type": "string", "minLength": 10, "maxLength": 4000},
                "evidence_transaction_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}
            }, "required": ["case_type", "subject_id", "rationale"]},
            required_scopes=frozenset({"agent:execute", "financial:case:create"}),
            risk_level="high", idempotent=True, handler=_financial_create_review_case,
            context_aware=True, requires_distinct_approver=True,
        ))

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool name is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())


async def _financial_get_customer(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from agent_runtime.db import FinancialAuditLog, FinancialCustomer, SessionLocal

    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        await _require_resource_grant(session, context, "customer", args["customer_id"], "read")
        row = (await session.execute(select(FinancialCustomer).where(
            FinancialCustomer.tenant_id == context["tenant_id"],
            FinancialCustomer.customer_id == args["customer_id"],
        ))).scalar_one_or_none()
        session.add(_audit_record(FinancialAuditLog, context, "CUSTOMER_PROFILE_READ", "financial_customer", args["customer_id"], {"found": row is not None}))
        await session.commit()
    if row is None:
        return {"found": False}
    return {"found": True, "customer_id": row.customer_id,
            "customer_type": row.customer_type, "country_code": row.country_code,
            "risk_rating": row.risk_rating, "kyc_status": row.kyc_status}


async def _financial_get_account(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from agent_runtime.db import FinancialAccount, FinancialAuditLog, SessionLocal

    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        await _require_resource_grant(session, context, "account", args["account_id"], "read")
        row = (await session.execute(select(FinancialAccount).where(
            FinancialAccount.tenant_id == context["tenant_id"],
            FinancialAccount.account_id == args["account_id"],
        ))).scalar_one_or_none()
        session.add(_audit_record(FinancialAuditLog, context, "ACCOUNT_SUMMARY_READ", "financial_account", args["account_id"], {"found": row is not None}))
        await session.commit()
    if row is None:
        return {"found": False}
    return {"found": True, "account_id": row.account_id, "customer_id": row.customer_id,
            "account_number_masked": row.account_number_masked, "account_type": row.account_type,
            "currency": row.currency, "balance_minor": row.balance_minor, "status": row.status}


async def _financial_list_transactions(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from agent_runtime.db import FinancialAuditLog, FinancialTransaction, SessionLocal

    stmt = select(FinancialTransaction).where(
        FinancialTransaction.tenant_id == context["tenant_id"],
        FinancialTransaction.account_id == args["account_id"],
    )
    if args.get("start_at"):
        start_at = datetime.fromisoformat(args["start_at"].replace("Z", "+00:00"))
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        stmt = stmt.where(FinancialTransaction.occurred_at >= start_at)
    if args.get("end_at"):
        end_at = datetime.fromisoformat(args["end_at"].replace("Z", "+00:00"))
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        stmt = stmt.where(FinancialTransaction.occurred_at <= end_at)
    stmt = stmt.order_by(FinancialTransaction.occurred_at.desc()).limit(args.get("limit", 50))
    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        await _require_resource_grant(session, context, "account", args["account_id"], "read")
        rows = list((await session.execute(stmt)).scalars())
        session.add(_audit_record(FinancialAuditLog, context, "TRANSACTIONS_READ", "financial_account", args["account_id"], {"count": len(rows)}))
        await session.commit()
    return {"count": len(rows), "transactions": [
        {"transaction_id": row.transaction_id, "occurred_at": row.occurred_at.isoformat(),
         "direction": row.direction, "amount_minor": row.amount_minor, "currency": row.currency,
         "counterparty_name": row.counterparty_name, "counterparty_country": row.counterparty_country,
         "channel": row.channel, "status": row.status, "reference": row.reference}
        for row in rows
    ]}


async def _financial_screen_transactions(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from agent_runtime.db import FinancialAuditLog, FinancialTransaction, SessionLocal
    from agent_runtime.finance.monitoring import detect_review_indicators

    from datetime import datetime, timedelta, timezone
    lookback_days = args.get("lookback_days", 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        await _require_resource_grant(session, context, "account", args["account_id"], "read")
        rows = list((await session.execute(select(FinancialTransaction).where(
            FinancialTransaction.tenant_id == context["tenant_id"],
            FinancialTransaction.account_id == args["account_id"],
            FinancialTransaction.occurred_at >= cutoff,
        ).order_by(FinancialTransaction.occurred_at.asc()).limit(5000))).scalars())
    transactions = [{"transaction_id": row.transaction_id, "occurred_at": row.occurred_at,
                     "direction": row.direction, "amount_minor": row.amount_minor,
                     "currency": row.currency, "reference": row.reference} for row in rows]
    result = detect_review_indicators(
        transactions,
        velocity_count=args.get("velocity_count", 4),
        velocity_window_minutes=args.get("velocity_window_minutes", 30),
    )
    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        session.add(_audit_record(FinancialAuditLog, context, "TRANSACTION_SCREENING_RUN", "financial_account", args["account_id"],
                                  {"transaction_count": len(rows), "indicator_count": result["indicator_count"]}))
        await session.commit()
    return {"account_id": args["account_id"], "lookback_days": lookback_days,
            "transactions_scanned": len(rows), "scan_limit": 5000, **result}


async def _financial_create_review_case(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID
    from sqlalchemy import select
    from agent_runtime.db import (FinancialAccount, FinancialAuditLog, FinancialCustomer,
                                  FinancialReviewCase, FinancialTransaction, SessionLocal)

    case_type = args["case_type"]
    subject_id = args["subject_id"]
    async with tenant_session(SessionLocal, context["tenant_id"]) as session:
        if context.get("idempotency_key"):
            existing_case = (await session.execute(select(FinancialReviewCase).where(
                FinancialReviewCase.tenant_id == context["tenant_id"],
                FinancialReviewCase.idempotency_key == context["idempotency_key"],
            ))).scalar_one_or_none()
            if existing_case is not None:
                return {"case_id": str(existing_case.case_id), "case_type": existing_case.case_type,
                        "subject_id": existing_case.subject_id, "status": existing_case.status,
                        "idempotent_replay": True}
        resource_type = {"KYC": "customer", "AML": "transaction", "TRANSACTION_REVIEW": "account"}[case_type]
        # Check user-to-resource authorization before checking whether the resource exists,
        # avoiding a cross-resource enumeration side channel.
        await _require_resource_grant(session, context, resource_type, subject_id, "case:create")
        model = {"KYC": FinancialCustomer, "AML": FinancialTransaction,
                 "TRANSACTION_REVIEW": FinancialAccount}[case_type]
        subject = (await session.execute(select(model).where(
            model.tenant_id == context["tenant_id"],
            getattr(model, {"KYC": "customer_id", "AML": "transaction_id", "TRANSACTION_REVIEW": "account_id"}[case_type]) == subject_id,
        ))).scalar_one_or_none()
        if subject is None:
            raise ValueError("case subject not found in the current tenant")
        evidence_ids = args.get("evidence_transaction_ids", [])
        if evidence_ids:
            evidence_rows = list((await session.execute(select(FinancialTransaction.transaction_id).where(
                FinancialTransaction.tenant_id == context["tenant_id"],
                FinancialTransaction.transaction_id.in_(evidence_ids),
            ))).scalars())
            if set(evidence_rows) != set(evidence_ids):
                raise ValueError("one or more evidence transactions are not accessible in the current tenant")
            for evidence_id in evidence_ids:
                await _require_resource_grant(session, context, "transaction", evidence_id, "read")
        case = FinancialReviewCase(
            tenant_id=context["tenant_id"], case_type=case_type, subject_id=subject_id,
            rationale=args["rationale"], evidence_transaction_ids=evidence_ids,
            status="OPEN", created_by=context["user_id"],
            run_id=UUID(context["run_id"]) if context.get("run_id") else None,
            idempotency_key=context.get("idempotency_key"),
        )
        session.add(case)
        await session.flush()
        session.add(FinancialAuditLog(
            tenant_id=context["tenant_id"], actor_user_id=context["user_id"],
            run_id=case.run_id, action="REVIEW_CASE_CREATED", resource_type="financial_review_case",
            resource_id=str(case.case_id),
            details={"case_type": case_type, "subject_id": subject_id, "status": "OPEN"},
        ))
        await session.commit()
        return {"case_id": str(case.case_id), "case_type": case.case_type,
                "subject_id": case.subject_id, "status": case.status,
                "message": "内部复核案件已创建；这不代表违规认定或监管申报。"}


registry = ToolRegistry()
mcp_server_status: dict[str, dict[str, Any]] = {}


def _mask_url(url: str) -> str:
    """脱敏 URL 中的 apiKey/token 等查询参数，避免凭证进入状态展示与日志。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
        sensitive = {"apikey", "api_key", "token", "access_token", "secret", "key"}
        query = [(k, "***" if k.lower() in sensitive else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return "<masked>"


async def configure_mcp_servers(config_json: str) -> list[str]:
    """Discover only explicitly allow-listed MCP tools from configured servers."""
    import json
    import logging

    from agent_runtime.tools.mcp_client import MCPToolClient

    logger = logging.getLogger(__name__)
    try:
        server_configs = json.loads(config_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("MCP_SERVERS_JSON must be a JSON array") from exc
    if not isinstance(server_configs, list):
        raise ValueError("MCP_SERVERS_JSON must be a JSON array")

    discovered: list[str] = []
    client = MCPToolClient()
    for config in server_configs:
        alias = config["name"]
        url = config["url"]
        record: dict[str, Any] = {
            "server_id": alias,
            "name": config.get("label") or alias,
            "transport": config.get("transport", "Streamable HTTP"),
            "status": "UNHEALTHY",
            "protocol_version": config.get("protocol_version", "2025-06-18"),
            "tools_count": 0,
            "resources_count": int(config.get("resources_count", 0)),
            "prompts_count": int(config.get("prompts_count", 0)),
            "last_connected_at": datetime.now(timezone.utc).isoformat(),
            "error_rate_pct": 100.0,
            "latency_ms": 0,
            "description": config.get("description") or f"MCP 服务 {alias}，由外部服务提供工具与资源。",
            "url": _mask_url(url),
        }
        mcp_server_status[alias] = record
        allow_tools = set(config.get("allow_tools", []))
        if not allow_tools:
            logger.warning("MCP server %s has no allow_tools; no tools exposed", alias)
            record["status"] = "DEGRADED"
            record["error_rate_pct"] = 0.0
            continue
        required_scopes = frozenset(config.get("required_scopes", ["agent:execute"]))
        risk_levels = config.get("risk_levels", {})
        try:
            started = datetime.now(timezone.utc)
            tools = await client.list_tools(url)
            record["latency_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            record["status"] = "HEALTHY"
            record["error_rate_pct"] = 0.0
            record["last_connected_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.exception("MCP discovery failed for server %s", alias)
            record["reason"] = _mask_url(str(exc)[:500])
            continue
        tool_schemas = config.get("tool_schemas", {}) or {}
        for remote_tool in tools:
            remote_name = remote_tool["name"]
            if remote_name not in allow_tools:
                continue
            public_name = f"{alias}.{remote_name}"
            if registry.get(public_name) is not None:
                logger.error("MCP tool %s collides with an existing built-in tool; skipping", public_name)
                continue
            # 部分 MCP 服务端不返回 inputSchema；允许配置显式 schema，让模型能正确构造参数。
            schema = tool_schemas.get(remote_name) or remote_tool.get("input_schema") or {"type": "object"}
            registry.register(
                ToolDefinition(
                    name=public_name,
                    display_name=remote_tool.get("title") or remote_tool.get("displayName") or f"MCP 工具：{remote_name}",
                    description=remote_tool.get("description") or f"由外部 MCP 服务提供，用于执行 {remote_name} 指定的能力。",
                    input_schema=schema,
                    required_scopes=required_scopes,
                    risk_level=risk_levels.get(remote_name, config.get("default_risk_level", "high")),
                    idempotent=bool(config.get("idempotent", False)),
                    mcp_server_url=url,
                    mcp_tool_name=remote_name,
                )
            )
            discovered.append(public_name)
        mcp_server_status[alias]["tools_count"] = sum(1 for name in discovered if name.startswith(f"{alias}."))
    return discovered
