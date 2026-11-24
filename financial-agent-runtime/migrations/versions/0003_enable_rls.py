"""enable tenant row level security

Revision ID: 0003_rls
Revises: 0002_identity
"""

from alembic import op

revision = "0003_rls"
down_revision = "0002_identity"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "agent_runs", "run_events", "workflow_outbox", "tool_invocations", "run_telemetry",
    "run_checkpoints", "worker_failures", "agent_memories", "financial_customers",
    "financial_accounts", "financial_transactions", "financial_review_cases",
    "financial_resource_grants", "financial_audit_logs", "organizations",
    "tenant_memberships", "user_roles", "resource_acls", "auth_audit_logs",
]


def upgrade():
    for table in TENANT_TABLES:
        policy = f"tenant_isolation_{table}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
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
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
