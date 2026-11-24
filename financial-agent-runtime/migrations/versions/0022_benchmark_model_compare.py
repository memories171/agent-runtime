"""seed model comparison benchmark experiments

Revision ID: 0022_benchmark_model_compare
Revises: 0021_analyst_audit_read
"""

from alembic import op
import sqlalchemy as sa

import json

revision = "0022_benchmark_model_compare"
down_revision = "0021_analyst_audit_read"
branch_labels = None
depends_on = None

TENANT_ID = "demo-bank"
BATCH = "BATCH-DEMO-2026-MODEL-COMPARE"
PROMPT_VERSION = "PROMPT-DEMO-v5.2"
DATASET = "finance-smoke-demo-120"

MODEL_METRICS = [
    {
        "key": "deepseek-chat",
        "model": "deepseek-chat",
        "model_version": "v3",
        "alpha": (86, 94, 76, 90, 0.3, 1.2, 430, 1180, 4.20, 4.88),
        "beta": (91, 96, 82, 93, 0.2, 0.8, 390, 1020, 4.50, 4.95),
    },
    {
        "key": "deepseek-reasoner",
        "model": "deepseek-reasoner",
        "model_version": "r1",
        "alpha": (90, 95, 88, 82, 0.2, 0.9, 610, 1640, 7.80, 8.67),
        "beta": (94, 97, 92, 86, 0.1, 0.6, 540, 1430, 8.20, 8.72),
    },
    {
        "key": "qwen3-235b",
        "model": "qwen3-235b-a22b",
        "model_version": "instruct",
        "alpha": (88, 95, 84, 91, 0.2, 0.8, 470, 1260, 5.10, 5.80),
        "beta": (92, 97, 89, 94, 0.2, 0.6, 420, 1100, 5.40, 5.87),
    },
    {
        "key": "gpt-4.1-mini",
        "model": "gpt-4.1-mini",
        "model_version": "2026-04",
        "alpha": (91, 96, 86, 94, 0.1, 0.5, 400, 1050, 5.60, 6.15),
        "beta": (95, 98, 91, 96, 0.1, 0.4, 360, 930, 5.90, 6.21),
    },
    {
        "key": "claude-sonnet-4",
        "model": "claude-sonnet-4",
        "model_version": "2026-05",
        "alpha": (93, 97, 90, 95, 0.1, 0.4, 520, 1380, 9.40, 10.11),
        "beta": (96, 98, 94, 97, 0.1, 0.3, 460, 1210, 9.80, 10.21),
    },
    {
        "key": "qwen3-14b",
        "model": "qwen3-14b",
        "model_version": "instruct",
        "alpha": (74, 86, 64, 78, 0.8, 2.4, 280, 760, 1.20, 1.62),
        "beta": (80, 90, 72, 83, 0.5, 1.6, 250, 680, 1.30, 1.63),
    },
]

HARNESSES = [
    ("alpha", "Harness-Alpha", "alpha-demo-2.0"),
    ("beta", "Harness-Beta", "beta-demo-2.2"),
]


def _rows():
    for item in MODEL_METRICS:
        for suffix, harness, harness_version in HARNESSES:
            (
                success, completion, recovery, tool, unauthorized, injection,
                duration, p95, cost, cost_per_success,
            ) = item[suffix]
            yield {
                "experiment_id": f"exp-model-2026-{item['key']}-{suffix}",
                "tenant_id": TENANT_ID,
                "batch": BATCH,
                "model": item["model"],
                "model_version": item["model_version"],
                "harness": harness,
                "harness_version": harness_version,
                "prompt_version": PROMPT_VERSION,
                "dataset": DATASET,
                "runs": 24,
                "task_success_rate": success,
                "task_completion_rate": completion,
                "recovery_success_rate": recovery,
                "tool_call_accuracy": tool,
                "unauthorized_access_rate": unauthorized,
                "prompt_injection_success_rate": injection,
                "avg_duration_seconds": duration,
                "p95_latency_ms": p95,
                "token_cost_usd": cost,
                "cost_per_success_usd": cost_per_success,
                "status": "COMPLETED",
                "metadata": json.dumps({"source": "model_comparison_seed", "demo": True}),
            }


INSERT_SQL = sa.text("""
    INSERT INTO benchmark_experiments (
        experiment_id, tenant_id, batch, model, model_version,
        harness, harness_version, prompt_version, dataset, runs,
        task_success_rate, task_completion_rate, recovery_success_rate,
        tool_call_accuracy, unauthorized_access_rate, prompt_injection_success_rate,
        avg_duration_seconds, p95_latency_ms, token_cost_usd, cost_per_success_usd,
        status, metadata
    ) VALUES (
        :experiment_id, :tenant_id, :batch, :model, :model_version,
        :harness, :harness_version, :prompt_version, :dataset, :runs,
        :task_success_rate, :task_completion_rate, :recovery_success_rate,
        :tool_call_accuracy, :unauthorized_access_rate, :prompt_injection_success_rate,
        :avg_duration_seconds, :p95_latency_ms, :token_cost_usd, :cost_per_success_usd,
        :status, :metadata
    )
    ON CONFLICT (experiment_id) DO NOTHING
""")


def upgrade():
    bind = op.get_bind()
    for row in _rows():
        bind.execute(INSERT_SQL, row)


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM benchmark_experiments WHERE batch = :batch"),
        {"batch": BATCH},
    )
