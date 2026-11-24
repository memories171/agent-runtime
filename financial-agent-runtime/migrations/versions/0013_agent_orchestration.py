"""Sub-Agent Orchestration 阶段 1：模型与持久化。

Revision ID: 0013_agent_orchestration
Revises: 0012_task_graph_versions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_agent_orchestration"
down_revision = "0012_task_graph_versions"
branch_labels = None
depends_on = None

NEW_TABLES = ("capability_tokens", "agent_delegations")
POLICY = """
current_setting('app.tenant_id', true) = tenant_id
OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver', 'agent_runtime')
"""


def _add_column(table: str, name: str, definition: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}"))


def _drop_column(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name}"))


def upgrade():
    _add_column("agent_runs", "run_kind", "VARCHAR(32) NOT NULL DEFAULT 'ROOT'")
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_runs_tenant_kind ON agent_runs (tenant_id, run_kind, created_at)"
    ))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS capability_tokens (
            token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL,
            parent_token_id UUID,
            subject_run_id UUID,
            subject_agent_id VARCHAR(128),
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            resource_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
            depth INTEGER NOT NULL DEFAULT 0,
            max_depth INTEGER NOT NULL DEFAULT 3,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            signature_hash VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_capability_token_tenant_token UNIQUE (tenant_id, token_id),
            CONSTRAINT fk_capability_token_run_tenant
                FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE,
            CONSTRAINT fk_capability_token_parent_tenant
                FOREIGN KEY (tenant_id, parent_token_id) REFERENCES capability_tokens(tenant_id, token_id) ON DELETE RESTRICT,
            CONSTRAINT fk_capability_token_subject_tenant
                FOREIGN KEY (tenant_id, subject_run_id) REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_capability_token_subject ON capability_tokens (tenant_id, subject_run_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_capability_token_parent ON capability_tokens (tenant_id, parent_token_id)"
    ))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agent_delegations (
            delegation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id VARCHAR(128) NOT NULL,
            parent_run_id UUID NOT NULL,
            child_run_id UUID NOT NULL,
            parent_agent_id VARCHAR(128) NOT NULL,
            child_agent_id VARCHAR(128) NOT NULL,
            capability_token_id UUID NOT NULL,
            delegation_key VARCHAR(256) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'ISSUED',
            graph_id VARCHAR(128),
            node_id VARCHAR(128),
            depth INTEGER NOT NULL DEFAULT 0,
            max_depth INTEGER NOT NULL DEFAULT 3,
            attempt INTEGER NOT NULL DEFAULT 1,
            timeout_seconds INTEGER NOT NULL DEFAULT 1800,
            deadline_at TIMESTAMPTZ,
            result_summary JSONB,
            error TEXT,
            artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            CONSTRAINT uq_agent_delegation_key UNIQUE (tenant_id, delegation_key),
            CONSTRAINT uq_agent_delegation_node_attempt UNIQUE (tenant_id, parent_run_id, node_id, attempt),
            CONSTRAINT fk_agent_delegation_parent_run_tenant
                FOREIGN KEY (tenant_id, parent_run_id) REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE,
            CONSTRAINT fk_agent_delegation_child_run_tenant
                FOREIGN KEY (tenant_id, child_run_id) REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE,
            CONSTRAINT fk_agent_delegation_token_tenant
                FOREIGN KEY (tenant_id, capability_token_id) REFERENCES capability_tokens(tenant_id, token_id) ON DELETE RESTRICT,
            CONSTRAINT fk_agent_delegation_graph_node_tenant
                FOREIGN KEY (tenant_id, graph_id, node_id) REFERENCES task_graph_nodes(tenant_id, graph_id, node_key) ON DELETE CASCADE
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agent_delegation_parent ON agent_delegations (tenant_id, parent_run_id, status)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agent_delegation_child ON agent_delegations (tenant_id, child_run_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agent_delegation_graph_node ON agent_delegations (tenant_id, graph_id, node_id)"
    ))

    _add_column("run_events", "parent_run_id", "UUID")
    _add_column("run_events", "delegation_id", "UUID")
    _add_column("run_events", "agent_id", "VARCHAR(128)")
    _add_column("graph_events", "agent_id", "VARCHAR(128)")
    _add_column("graph_events", "delegation_id", "UUID")
    _add_column("graph_events", "capability_token_id", "UUID")
    _add_column("graph_events", "depth", "INTEGER")
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_run_event_delegation ON run_events (tenant_id, delegation_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_graph_event_delegation ON graph_events (tenant_id, delegation_id)"
    ))

    for table in NEW_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_policies
                               WHERE tablename = '{table}'
                                 AND policyname = 'tenant_isolation_{table}') THEN
                    CREATE POLICY tenant_isolation_{table} ON {table}
                        USING ({POLICY}) WITH CHECK ({POLICY});
                END IF;
            END $$;
        """))


def downgrade():
    for table in NEW_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.execute(sa.text("DROP INDEX IF EXISTS ix_graph_event_delegation"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_run_event_delegation"))
    for table, columns in (
        ("graph_events", ("agent_id", "delegation_id", "capability_token_id", "depth")),
        ("run_events", ("parent_run_id", "delegation_id", "agent_id")),
    ):
        for column in columns:
            _drop_column(table, column)

    op.execute(sa.text("DROP TABLE IF EXISTS agent_delegations"))
    op.execute(sa.text("DROP TABLE IF EXISTS capability_tokens"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_runs_tenant_kind"))
    _drop_column("agent_runs", "run_kind")
