"""add soft delete for agent runs

Revision ID: 0005_run_delete
Revises: 0004_seed
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_run_delete"
down_revision = "0004_seed"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    bind = op.get_bind()
    bind.execute(
        sa.text("INSERT INTO permissions (permission_id, description) VALUES (:p, :d) ON CONFLICT DO NOTHING"),
        {"p": "run:delete", "d": "Delete agent runs"},
    )
    for role_id in ("tenant_admin", "analyst"):
        bind.execute(
            sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p) ON CONFLICT DO NOTHING"),
            {"r": role_id, "p": "run:delete"},
        )


def downgrade():
    bind = op.get_bind()
    for role_id in ("tenant_admin", "analyst"):
        bind.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :r AND permission_id = 'run:delete'"),
            {"r": role_id},
        )
    bind.execute(sa.text("DELETE FROM permissions WHERE permission_id = 'run:delete'"))
    op.drop_column("agent_runs", "deleted_at")
