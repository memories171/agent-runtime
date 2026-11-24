"""Agent Tree monitoring fields: persistent root_run_id and updated_at.

Revision ID: 0015_agent_monitoring_tree
Revises: 0014_agent_delegation_workflow
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_agent_monitoring_tree"
down_revision = "0014_agent_delegation_workflow"
branch_labels = None
depends_on = None

INDEXES = (
    "ix_agent_delegation_root",
    "ix_agent_delegation_updated",
)


def upgrade():
    op.execute(sa.text(
        "ALTER TABLE agent_delegations ADD COLUMN IF NOT EXISTS root_run_id UUID"
    ))
    op.execute(sa.text(
        "ALTER TABLE agent_delegations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ))
    # Backfill root_run_id by walking parent_run_id up to the topmost ancestor.
    # tenant_id is part of the join so cross-tenant links can never be followed.
    # depth < 32 bounds the recursion so a corrupt cycle cannot hang the migration.
    op.execute(sa.text("""
        WITH RECURSIVE lineage AS (
            SELECT delegation_id, tenant_id, parent_run_id AS ancestor_run_id, 1 AS depth
            FROM agent_delegations
            UNION ALL
            SELECT l.delegation_id, l.tenant_id, p.parent_run_id, l.depth + 1
            FROM lineage l
            JOIN agent_delegations p
              ON p.tenant_id = l.tenant_id AND p.child_run_id = l.ancestor_run_id
            WHERE l.depth < 32
        )
        UPDATE agent_delegations d
        SET root_run_id = (
            SELECT l.ancestor_run_id FROM lineage l
            WHERE l.delegation_id = d.delegation_id
            ORDER BY l.depth DESC
            LIMIT 1
        )
        WHERE d.root_run_id IS NULL
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agent_delegation_root "
        "ON agent_delegations (tenant_id, root_run_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_agent_delegation_updated "
        "ON agent_delegations (tenant_id, updated_at)"
    ))


def downgrade():
    for name in INDEXES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))
    op.execute(sa.text("ALTER TABLE agent_delegations DROP COLUMN IF EXISTS updated_at"))
    op.execute(sa.text("ALTER TABLE agent_delegations DROP COLUMN IF EXISTS root_run_id"))
