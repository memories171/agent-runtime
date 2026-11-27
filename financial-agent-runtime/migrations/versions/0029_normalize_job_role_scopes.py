"""normalize job role scopes after historical additive grants

Revision ID: 0029_normalize_job_role_scopes
Revises: 0028_seed_job_role_users
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_normalize_job_role_scopes"
down_revision = "0028_seed_job_role_users"
branch_labels = None
depends_on = None

ROLE_PERMISSIONS = {
    "general_manager": {
        "platform:manage", "user:manage", "security:read", "audit:read",
        "financial:audit:read", "runs:read:tenant", "skills:manage",
        "approval:read", "knowledge:read", "knowledge:write",
    },
    "analyst": {
        "agent:execute", "run:create", "run:read", "run:pause", "run:resume", "run:context:read",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:monitoring:run", "financial:case:create", "financial:case:read",
        "memory:read", "memory:read:team", "memory:write", "memory:write:team",
        "knowledge:read", "tool:execute",
    },
    "risk_controller": {
        "run:read", "runs:read:tenant", "runs:cancel:tenant",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:case:read", "financial:case:review", "financial:case:approve",
        "approval:read", "approval:approve", "financial:audit:read", "audit:read",
        "knowledge:read", "data:sensitive", "tool:execute",
    },
}


def upgrade():
    bind = op.get_bind()
    for role, expected in ROLE_PERMISSIONS.items():
        current = {
            row[0] for row in bind.execute(
                sa.text("SELECT permission_id FROM role_permissions WHERE role_id = :r"),
                {"r": role},
            )
        }
        for permission in current - expected:
            bind.execute(
                sa.text("DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"),
                {"r": role, "p": permission},
            )
        for permission in expected - current:
            bind.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p) ON CONFLICT DO NOTHING"),
                {"r": role, "p": permission},
            )


def downgrade():
    # Scope restoration is intentionally not attempted: historical additive
    # grants differ by environment and must not be reintroduced automatically.
    pass
