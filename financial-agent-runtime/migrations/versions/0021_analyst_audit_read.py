"""grant financial audit read to analyst role

Revision ID: 0021_analyst_audit_read
Revises: 0020_demo_read_scopes
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_analyst_audit_read"
down_revision = "0020_demo_read_scopes"
branch_labels = None
depends_on = None

ROLE_ID = "analyst"
PERMISSION_ID = "financial:audit:read"


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "VALUES (:r, :p) ON CONFLICT DO NOTHING"
        ),
        {"r": ROLE_ID, "p": PERMISSION_ID},
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"
        ),
        {"r": ROLE_ID, "p": PERMISSION_ID},
    )
