"""Context Manager: versioned ContextPacks, rolling summaries and artifacts.

Revision ID: 0009_context_manager
Revises: 0008_pause_approval_outbox
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_context_manager"
down_revision = "0008_pause_approval_outbox"
branch_labels = None
depends_on = None


NEW_TABLES = ("context_packs", "context_summaries", "context_artifacts")


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS context_packs (
            pack_row_id UUID PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            context_pack_id VARCHAR(64) NOT NULL,
            schema_version VARCHAR(16) NOT NULL DEFAULT '1.0',
            model_name VARCHAR(128),
            event_cursor INTEGER NOT NULL DEFAULT 0,
            pack_json JSON NOT NULL,
            compression_count INTEGER NOT NULL DEFAULT 0,
            compression_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
            degraded BOOLEAN NOT NULL DEFAULT FALSE,
            degradation_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, context_pack_id)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_context_pack_run_created ON context_packs(tenant_id, run_id, created_at)"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS context_summaries (
            summary_id UUID PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            idempotency_key VARCHAR(64) NOT NULL,
            source_event_start INTEGER NOT NULL,
            source_event_end INTEGER NOT NULL,
            source_event_ids JSON NOT NULL,
            summary_version INTEGER NOT NULL,
            summary_content TEXT NOT NULL,
            model_version VARCHAR(128),
            content_hash VARCHAR(64) NOT NULL,
            key_facts JSON NOT NULL DEFAULT '[]'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, idempotency_key)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_context_summary_run_version ON context_summaries(tenant_id, run_id, summary_version)"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS context_artifacts (
            artifact_id UUID PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            object_ref VARCHAR(512) NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            source_event_id VARCHAR(64),
            acl JSON NOT NULL DEFAULT '[]'::json,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_context_artifact_run ON context_artifacts(tenant_id, run_id, created_at)"))

    # RLS for every new tenant table (same policy shape as 0003 / 0008).
    bind = op.get_bind()
    for table in NEW_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver', 'agent_runtime')
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver', 'agent_runtime')
            )
        """))

    # Read permission for the new context APIs. Only grant to roles that exist in
    # this deployment (seed roles vary by realm), guarded to avoid FK violations.
    op.execute(sa.text("INSERT INTO permissions(permission_id, description) VALUES ('run:context:read', 'read agent run context packs') ON CONFLICT (permission_id) DO NOTHING"))
    bind.execute(sa.text("""
        INSERT INTO role_permissions(role_id, permission_id)
        SELECT r.role_id, 'run:context:read' FROM roles r
        WHERE r.role_id IN ('tenant_admin', 'analyst', 'auditor', 'compliance_officer', 'read_only_user')
        ON CONFLICT DO NOTHING
    """))


def downgrade():
    for table in NEW_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP TABLE IF EXISTS context_artifacts"))
    op.execute(sa.text("DROP TABLE IF EXISTS context_summaries"))
    op.execute(sa.text("DROP TABLE IF EXISTS context_packs"))
