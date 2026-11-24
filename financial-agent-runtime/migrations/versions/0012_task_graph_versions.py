"""Immutable snapshots for Task Graph versions.

Revision ID: 0012_task_graph_versions
Revises: 0011_graph_tenant_fk
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_task_graph_versions"
down_revision = "0011_graph_tenant_fk"
branch_labels = None
depends_on = None

POLICY = """
current_setting('app.tenant_id', true) = tenant_id
OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver', 'agent_runtime')
"""


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_graph_versions (
            version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(128) NOT NULL,
            graph_id TEXT NOT NULL,
            graph_version INTEGER NOT NULL,
            definition_json JSONB NOT NULL,
            validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
            planner_model VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_task_graph_version UNIQUE (tenant_id, graph_id, graph_version),
            CONSTRAINT fk_task_graph_version_graph
                FOREIGN KEY (tenant_id, graph_id) REFERENCES task_graphs(tenant_id, graph_id)
                ON DELETE CASCADE
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_graph_version_lookup "
                       "ON task_graph_versions (tenant_id, graph_id, graph_version)"))
    op.execute(sa.text("ALTER TABLE task_graph_versions ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE task_graph_versions FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_policies
                           WHERE tablename = 'task_graph_versions'
                             AND policyname = 'tenant_isolation_task_graph_versions') THEN
                CREATE POLICY tenant_isolation_task_graph_versions ON task_graph_versions
                    USING ({POLICY}) WITH CHECK ({POLICY});
            END IF;
        END $$;
    """))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS task_graph_versions"))
