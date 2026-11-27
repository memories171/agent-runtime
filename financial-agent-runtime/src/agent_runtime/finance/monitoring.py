from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_time(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def detect_review_indicators(
    transactions: list[dict[str, Any]],
    *,
    velocity_count: int = 4,
    velocity_window_minutes: int = 30,
) -> dict[str, Any]:
    """Detect explainable operational review indicators on one account's transactions.

    This is a demo rule set. It is not a legal AML assessment, regulatory threshold,
    customer risk decision, or substitute for an institution-approved monitoring engine.

    Production use requires an explicit, approved opt-in.
    """
    from agent_runtime.config import settings

    if settings.is_production and not settings.finance_monitoring_production_rules:
        raise RuntimeError(
            "Demo transaction screening rules are disabled in production. "
            "Set FINANCE_MONITORING_PRODUCTION_RULES=true with an approved reference."
        )
    normalized = []
    for item in transactions:
        row = dict(item)
        row["_time"] = _parse_time(row["occurred_at"])
        normalized.append(row)
    normalized.sort(key=lambda row: row["_time"])

    indicators: list[dict[str, Any]] = []
    references = Counter(row.get("reference") for row in normalized if row.get("reference"))
    for reference, count in references.items():
        if count > 1:
            indicators.append({
                "rule_code": "DUPLICATE_REFERENCE",
                "severity": "medium",
                "transaction_ids": [r["transaction_id"] for r in normalized if r.get("reference") == reference],
                "evidence": {"reference": reference, "occurrences": count},
                "explanation": "相同交易参考号出现多次，需要核对是否为重复入账或合法拆分。",
            })

    window = timedelta(minutes=velocity_window_minutes)
    left = 0
    debit_count = 0
    for right, row in enumerate(normalized):
        while left <= right and row["_time"] - normalized[left]["_time"] > window:
            if normalized[left].get("direction") == "debit":
                debit_count -= 1
            left += 1
        if row.get("direction") == "debit":
            debit_count += 1
        if debit_count >= velocity_count:
            debit_cluster = [candidate for candidate in normalized[left:right + 1]
                             if candidate.get("direction") == "debit"]
            ids = [candidate["transaction_id"] for candidate in debit_cluster]
            indicators.append({
                "rule_code": "HIGH_DEBIT_VELOCITY",
                "severity": "medium",
                "transaction_ids": ids,
                "evidence": {"count": len(debit_cluster), "window_minutes": velocity_window_minutes},
                "explanation": f"{velocity_window_minutes} 分钟窗口内出现 {len(debit_cluster)} 笔借记交易，超过演示规则阈值。",
            })
            break

    return {
        "indicator_count": len(indicators),
        "review_required": bool(indicators),
        "indicators": indicators,
        "disclaimer": "仅为可解释的运营复核提示，不构成洗钱认定、客户风险结论或监管申报建议。",
    }
