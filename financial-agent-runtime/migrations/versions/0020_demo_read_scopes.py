"""grant read-only scopes needed by approval and security console

Revision ID: 0020_demo_read_scopes
Revises: 0019_knowledge
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_demo_read_scopes"
down_revision = "0019_knowledge"
branch_labels = None
depends_on = None

NEW_PERMISSIONS = ["approval:read"]

ROLE_GRANTS = {
    "analyst": ["approval:read", "security:read"],
    "compliance_officer": ["approval:read"],
    "auditor": ["approval:read"],
}


def upgrade():
    bind = op.get_bind()
    for permission in NEW_PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (permission_id, description) "
                "VALUES (:p, :d) ON CONFLICT DO NOTHING"
            ),
            {"p": permission, "d": permission},
        )
    for role_id, permissions in ROLE_GRANTS.items():
        for permission in permissions:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": role_id, "p": permission},
            )


def downgrade():
    bind = op.get_bind()
    for role_id, permissions in ROLE_GRANTS.items():
        for permission in permissions:
            bind.execute(
                sa.text(
                    "DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"
                ),
                {"r": role_id, "p": permission},
            )
    for permission in NEW_PERMISSIONS:
        bind.execute(
            sa.text("DELETE FROM permissions WHERE permission_id = :p"),
            {"p": permission},
        )
