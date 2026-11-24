from agent_runtime.finance.monitoring import detect_review_indicators
from agent_runtime.tools.registry import registry


def test_velocity_rule_detects_cluster():
    rows = [
        {"transaction_id": f"t{i}", "occurred_at": f"2026-01-01T10:{i*5:02d}:00Z",
         "direction": "debit", "amount_minor": 100, "currency": "USD", "reference": f"r{i}"}
        for i in range(4)
    ]
    result = detect_review_indicators(rows)
    assert result["review_required"] is True
    assert any(item["rule_code"] == "HIGH_DEBIT_VELOCITY" for item in result["indicators"])


def test_duplicate_reference_rule_detects_repeat():
    rows = [
        {"transaction_id": "a", "occurred_at": "2026-01-01T10:00:00Z", "direction": "debit", "reference": "same"},
        {"transaction_id": "b", "occurred_at": "2026-01-02T10:00:00Z", "direction": "debit", "reference": "same"},
    ]
    result = detect_review_indicators(rows)
    assert result["review_required"] is True
    assert result["indicators"][0]["rule_code"] == "DUPLICATE_REFERENCE"


def test_financial_case_creation_requires_dual_control():
    tool = registry.get("financial.create_review_case")
    assert tool is not None
    assert tool.risk_level == "high"
    assert tool.requires_distinct_approver is True
    assert "financial:case:create" in tool.required_scopes


def test_demo_planner_only_runs_financial_screen_on_explicit_demo_phrase():
    from agent_runtime.agent.model import ModelAdapter

    decision = ModelAdapter._demo_decision({"prompt": "金融监测演示：ACC-DEMO-001", "history": []})
    assert decision.action == "tool_call"
    assert decision.tool.name == "financial.screen_transactions"
    assert decision.tool.arguments["account_id"] == "ACC-DEMO-001"


def test_demo_planner_does_not_infer_financial_actions_from_unrelated_prompt():
    from agent_runtime.agent.model import ModelAdapter

    decision = ModelAdapter._demo_decision({"prompt": "请总结昨天的会议", "history": []})
    assert decision.action == "tool_call"
    assert decision.tool.name == "demo.echo"
