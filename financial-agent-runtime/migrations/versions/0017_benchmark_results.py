"""benchmark experiments and ablations

Revision ID: 0017_benchmark
Revises: 0016_agent_tree_repair
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_benchmark"
down_revision = "0016_repair_tree_root_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "benchmark_experiments",
        sa.Column("experiment_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("batch", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("harness", sa.String(128), nullable=False),
        sa.Column("harness_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("dataset", sa.String(256), nullable=False, server_default=""),
        sa.Column("runs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("task_success_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("task_completion_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("recovery_success_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("tool_call_accuracy", sa.Float, nullable=False, server_default="0"),
        sa.Column("unauthorized_access_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("prompt_injection_success_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("avg_duration_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("p95_latency_ms", sa.Float, nullable=False, server_default="0"),
        sa.Column("token_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("cost_per_success_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("metadata", sa.JSON, nullable=False, server_default="{}"),
    )
    op.create_index("ix_benchmark_tenant_created", "benchmark_experiments", ["tenant_id", "created_at"])

    op.create_table(
        "benchmark_ablations",
        sa.Column("ablation_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("experiment_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("removed_component", sa.String(128), nullable=False),
        sa.Column("success_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("delta_pp", sa.Float, nullable=False, server_default="0"),
        sa.Column("recovery_success_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("unauthorized_access_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ablation_tenant_created", "benchmark_ablations", ["tenant_id", "created_at"])

    for table in ("benchmark_experiments", "benchmark_ablations"):
        policy = f"tenant_isolation_{table}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {policy} ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
            )
        """)


def downgrade():
    for table in ("benchmark_experiments", "benchmark_ablations"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
