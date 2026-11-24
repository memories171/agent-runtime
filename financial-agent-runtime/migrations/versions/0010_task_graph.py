"""Task Graph Engine: versioned graph definitions, node states and graph events.

Revision ID: 0010_task_graph
Revises: 0009_context_manager
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_task_graph"
down_revision = "0009_context_manager"
branch_labels = None
depends_on = None

NEW_TABLES = ("graph_events", "task_graph_edges", "task_graph_nodes", "task_graphs")


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_graphs (
            graph_id VARCHAR(128) PRIMARY KEY,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            graph_version INTEGER NOT NULL DEFAULT 1,
            graph_status VARCHAR(32) NOT NULL DEFAULT 'PLANNING',
            root_goal TEXT NOT NULL,
            acceptance_criteria JSON NOT NULL DEFAULT '[]'::json,
            max_concurrency INTEGER NOT NULL DEFAULT 5,
            failure_policy VARCHAR(32) NOT NULL DEFAULT 'CONTINUE_INDEPENDENT',
            planner_model VARCHAR(128),
            validation_report JSON NOT NULL DEFAULT '{}'::json,
            definition_json JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, run_id, graph_version)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_graph_tenant_run ON task_graphs(tenant_id, run_id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_graph_tenant_status ON task_graphs(tenant_id, graph_status)"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_graph_nodes (
            node_id UUID PRIMARY KEY,
            graph_id VARCHAR(128) NOT NULL REFERENCES task_graphs(graph_id) ON DELETE CASCADE,
            tenant_id VARCHAR(128) NOT NULL,
            node_key VARCHAR(128) NOT NULL,
            node_type VARCHAR(32) NOT NULL DEFAULT 'task',
            name VARCHAR(200) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            input_schema JSON NOT NULL DEFAULT '{}'::json,
            output_schema JSON NOT NULL DEFAULT '{}'::json,
            dependencies JSON NOT NULL DEFAULT '[]'::json,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            retry_policy JSON NOT NULL DEFAULT '{}'::json,
            timeout_seconds INTEGER NOT NULL DEFAULT 300,
            required_permissions JSON NOT NULL DEFAULT '[]'::json,
            context_policy VARCHAR(32) NOT NULL DEFAULT 'summary_only',
            execution_order INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            result_json JSON,
            token_input INTEGER NOT NULL DEFAULT 0,
            token_output INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            artifact_refs JSON NOT NULL DEFAULT '[]'::json,
            context_pack_id VARCHAR(128),
            source_event_ids JSON NOT NULL DEFAULT '[]'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, graph_id, node_key)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_graph_node_status ON task_graph_nodes(tenant_id, graph_id, status)"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task_graph_edges (
            edge_id UUID PRIMARY KEY,
            graph_id VARCHAR(128) NOT NULL REFERENCES task_graphs(graph_id) ON DELETE CASCADE,
            tenant_id VARCHAR(128) NOT NULL,
            edge_key VARCHAR(128) NOT NULL,
            source_node_id VARCHAR(128) NOT NULL,
            target_node_id VARCHAR(128) NOT NULL,
            dependency_type VARCHAR(32) NOT NULL DEFAULT 'SUCCESS',
            condition JSON,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, graph_id, edge_key)
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS graph_events (
            event_id UUID PRIMARY KEY,
            graph_id VARCHAR(128) NOT NULL REFERENCES task_graphs(graph_id) ON DELETE CASCADE,
            tenant_id VARCHAR(128) NOT NULL,
            run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            sequence_no BIGINT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            node_id VARCHAR(128),
            parent_node_id VARCHAR(128),
            execution_duration_ms INTEGER NOT NULL DEFAULT 0,
            token_usage JSON NOT NULL DEFAULT '{}'::json,
            payload JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, graph_id, sequence_no)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_graph_event_graph_seq ON graph_events(tenant_id, graph_id, sequence_no)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_graph_event_run_created ON graph_events(tenant_id, run_id, created_at)"))

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

    op.execute(sa.text("INSERT INTO permissions(permission_id, description) VALUES ('graph:read', 'read task graph definitions and events') ON CONFLICT (permission_id) DO NOTHING"))
    bind.execute(sa.text("""
        INSERT INTO role_permissions(role_id, permission_id)
        SELECT r.role_id, 'graph:read' FROM roles r
        WHERE r.role_id IN ('tenant_admin', 'analyst', 'auditor', 'compliance_officer', 'read_only_user')
        ON CONFLICT DO NOTHING
    """))


def downgrade():
    for table in NEW_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP TABLE IF EXISTS graph_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_graph_edges"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_graph_nodes"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_graphs"))
