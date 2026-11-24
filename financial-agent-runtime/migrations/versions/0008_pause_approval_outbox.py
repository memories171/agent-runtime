"""reliable pause/resume, signal outbox and approval bindings

Revision ID: 0008_pause_approval_outbox
Revises: 0007_tool_invocation_recovery
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_pause_approval_outbox"
down_revision = "0007_tool_invocation_recovery"
branch_labels = None
depends_on = None


def upgrade():
    # Agent runs: optimistic lock + pause control state (RunStatus unchanged).
    op.execute(sa.text("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0"))
    op.execute(sa.text("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS pause_state VARCHAR(32) NOT NULL DEFAULT 'NONE'"))

    # Workflow outbox: multiple signal commands per run. Drop the run-id-only unique constraint.
    op.execute(sa.text("ALTER TABLE workflow_outbox DROP CONSTRAINT IF EXISTS uq_workflow_outbox_run"))
    op.execute(sa.text("ALTER TABLE workflow_outbox ADD COLUMN IF NOT EXISTS command_id VARCHAR(256)"))
    op.execute(sa.text("ALTER TABLE workflow_outbox ADD COLUMN IF NOT EXISTS command_type VARCHAR(32) NOT NULL DEFAULT 'START_WORKFLOW'"))
    # Existing START rows get a stable command id derived from run id.
    op.execute(sa.text("UPDATE workflow_outbox SET command_id = 'start:' || run_id::text WHERE command_id IS NULL"))
    op.execute(sa.text("ALTER TABLE workflow_outbox ALTER COLUMN command_id SET NOT NULL"))
    op.execute(sa.text(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_outbox_command') THEN "
        "ALTER TABLE workflow_outbox ADD CONSTRAINT uq_outbox_command UNIQUE (command_id); "
        "END IF; END $$;"
    ))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_outbox_command_type ON workflow_outbox(command_type, status, next_attempt_at)"))

    # Approval bindings: strong, verifiable binding for the existing approval signal.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS approval_bindings (
            approval_id UUID PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            tool_call_id VARCHAR(128) NOT NULL,
            tool_name VARCHAR(256) NOT NULL,
            target_resource_id VARCHAR(256) NOT NULL DEFAULT '',
            operation_type VARCHAR(128) NOT NULL DEFAULT '',
            canonical_arguments TEXT NOT NULL DEFAULT '{}',
            canonical_arguments_hash VARCHAR(64) NOT NULL,
            action_hash VARCHAR(64) NOT NULL,
            requested_by VARCHAR(128) NOT NULL,
            approver_id VARCHAR(128),
            policy_version VARCHAR(64) NOT NULL DEFAULT 'v1',
            risk_level VARCHAR(32) NOT NULL DEFAULT 'HIGH',
            nonce VARCHAR(128) NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            approval_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            signature_reference VARCHAR(256),
            reason VARCHAR(2000),
            consumed_at TIMESTAMPTZ,
            version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, nonce),
            UNIQUE (tenant_id, run_id, tool_call_id, action_hash)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_approval_run ON approval_bindings(run_id, created_at DESC)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_approval_status ON approval_bindings(approval_status, expires_at)"))

    # RLS for the new tenant table (same policy shape as 0003).
    op.execute(sa.text("ALTER TABLE approval_bindings ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE approval_bindings FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_approval_bindings ON approval_bindings"))
    op.execute(sa.text("""
        CREATE POLICY tenant_isolation_approval_bindings ON approval_bindings
        USING (
            tenant_id = current_setting('app.tenant_id', true)
            OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)
            OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
        )
    """))

    # Permissions for pause/resume (permission_id is the code itself, see 0004).
    op.execute(sa.text("INSERT INTO permissions(permission_id, description) VALUES ('run:pause', 'pause a running agent run') ON CONFLICT (permission_id) DO NOTHING"))
    op.execute(sa.text("INSERT INTO permissions(permission_id, description) VALUES ('run:resume', 'resume a paused agent run') ON CONFLICT (permission_id) DO NOTHING"))
    bind = op.get_bind()
    for role in ("tenant_admin", "analyst"):
        for perm in ("run:pause", "run:resume"):
            bind.execute(sa.text("INSERT INTO role_permissions(role_id, permission_id) VALUES (:r, :p) ON CONFLICT DO NOTHING"), {"r": role, "p": perm})


def downgrade():
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_approval_bindings ON approval_bindings"))
    op.execute(sa.text("ALTER TABLE approval_bindings NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE approval_bindings DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP TABLE IF EXISTS approval_bindings"))
    op.drop_index("ix_outbox_command_type", table_name="workflow_outbox")
    op.execute(sa.text("ALTER TABLE workflow_outbox DROP CONSTRAINT IF EXISTS uq_outbox_command"))
    op.execute(sa.text("ALTER TABLE workflow_outbox DROP COLUMN IF EXISTS command_type"))
    op.execute(sa.text("ALTER TABLE workflow_outbox DROP COLUMN IF EXISTS command_id"))
    op.execute(sa.text(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_workflow_outbox_run') THEN "
        "ALTER TABLE workflow_outbox ADD CONSTRAINT uq_workflow_outbox_run UNIQUE (run_id); "
        "END IF; END $$;"
    ))
    op.execute(sa.text("ALTER TABLE agent_runs DROP COLUMN IF EXISTS pause_state"))
    op.execute(sa.text("ALTER TABLE agent_runs DROP COLUMN IF EXISTS version"))
    op.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN ('run:pause','run:resume')"))
    op.execute(sa.text("DELETE FROM permissions WHERE permission_id IN ('run:pause','run:resume')"))
