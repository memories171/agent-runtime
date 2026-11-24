"""baseline existing runtime tables

Revision ID: 0001_baseline
Revises:
"""

from alembic import op

from agent_runtime.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_TABLES = {
    "agent_runs", "run_events", "workflow_outbox", "tool_invocations", "run_telemetry",
    "run_checkpoints", "worker_failures", "skill_states", "agent_memories",
    "financial_customers", "financial_accounts", "financial_transactions",
    "financial_review_cases", "financial_resource_grants", "financial_audit_logs",
}


def upgrade():
    tables = [Base.metadata.tables[name] for name in BASELINE_TABLES]
    Base.metadata.create_all(op.get_bind(), tables=tables)


def downgrade():
    pass
