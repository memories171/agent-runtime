"""Track deterministic Child Workflow IDs on agent_delegations.

Revision ID: 0014_agent_delegation_workflow
Revises: 0013_agent_orchestration
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_agent_delegation_workflow"
down_revision = "0013_agent_orchestration"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text(
        "ALTER TABLE agent_delegations ADD COLUMN IF NOT EXISTS child_workflow_id VARCHAR(256)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_delegation_child_workflow "
        "ON agent_delegations (tenant_id, child_workflow_id) WHERE child_workflow_id IS NOT NULL"
    ))


def downgrade():
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_delegation_child_workflow"))
    op.execute(sa.text("ALTER TABLE agent_delegations DROP COLUMN IF EXISTS child_workflow_id"))
