"""Enforce graph tenant consistency with composite foreign keys.

Revision ID: 0011_graph_tenant_fk
Revises: 0010_task_graph
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_graph_tenant_fk"
down_revision = "0010_task_graph"
branch_labels = None
depends_on = None

TABLES = ("task_graph_nodes", "task_graph_edges", "graph_events")


def _add_constraint(table: str, name: str, definition: str) -> None:
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{name}' AND conrelid = '{table}'::regclass
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} {definition};
            END IF;
        END $$;
    """))


def _drop_constraint(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def upgrade():
    _add_constraint(
        "task_graphs", "uq_task_graph_tenant_graph",
        "UNIQUE (tenant_id, graph_id)",
    )
    _add_constraint(
        "agent_runs", "uq_agent_run_tenant_run",
        "UNIQUE (tenant_id, run_id)",
    )
    for table in TABLES:
        _add_constraint(
            table, f"fk_{table}_graph_tenant",
            "FOREIGN KEY (tenant_id, graph_id) REFERENCES task_graphs(tenant_id, graph_id) ON DELETE CASCADE",
        )
    _add_constraint(
        "graph_events", "fk_graph_events_run_tenant",
        "FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs(tenant_id, run_id) ON DELETE CASCADE",
    )


def downgrade():
    _drop_constraint("graph_events", "fk_graph_events_run_tenant")
    for table in TABLES:
        _drop_constraint(table, f"fk_{table}_graph_tenant")
    _drop_constraint("agent_runs", "uq_agent_run_tenant_run")
    _drop_constraint("task_graphs", "uq_task_graph_tenant_graph")
