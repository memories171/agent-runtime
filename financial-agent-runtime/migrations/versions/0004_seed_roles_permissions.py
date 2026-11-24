"""seed default roles and permissions

Revision ID: 0004_seed
Revises: 0003_rls
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_seed"
down_revision = "0003_rls"
branch_labels = None
depends_on = None

PERMISSIONS = [
    "agent:execute", "run:create", "run:read", "run:pause", "run:resume",
    "run:manage:tenant", "runs:read:tenant", "runs:manage:tenant",
    "memory:read", "memory:read:team", "memory:read:organization",
    "memory:write", "memory:write:team", "memory:write:organization", "memory:write:tenant",
    "transaction:read", "financial:customer:read", "financial:account:read",
    "financial:transactions:read", "financial:monitoring:run",
    "financial:case:create", "financial:case:read", "financial:case:approve",
    "financial:audit:read", "approval:approve", "audit:read", "user:manage",
    "security:read", "platform:manage", "skills:manage", "tool:execute",
    "data:sensitive", "resource:manage:tenant",
]

ROLES = {
    "tenant_admin": ("Tenant Admin", list(PERMISSIONS)),
    "analyst": ("Analyst", [
        "agent:execute", "run:create", "run:read", "runs:read:tenant",
        "memory:read", "memory:read:team", "memory:write", "memory:write:team",
        "transaction:read", "financial:customer:read", "financial:account:read",
        "financial:transactions:read", "financial:monitoring:run",
        "financial:case:create", "financial:case:read", "tool:execute",
    ]),
    "compliance_officer": ("Compliance Officer", [
        "run:read", "runs:read:tenant", "memory:read", "memory:read:team",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:case:read", "financial:case:approve", "approval:approve",
        "financial:audit:read", "audit:read", "tool:execute", "data:sensitive",
    ]),
    "auditor": ("Auditor", [
        "runs:read:tenant", "memory:read", "financial:audit:read", "audit:read",
        "security:read", "tool:execute", "data:sensitive",
    ]),
    "read_only_user": ("Read Only User", [
        "run:read", "runs:read:tenant", "memory:read",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:case:read",
    ]),
}


def upgrade():
    bind = op.get_bind()
    for permission in PERMISSIONS:
        bind.execute(
            sa.text("INSERT INTO permissions (permission_id, description) VALUES (:p, :d) ON CONFLICT DO NOTHING"),
            {"p": permission, "d": permission},
        )
    for role_id, (name, permissions) in ROLES.items():
        bind.execute(
            sa.text("INSERT INTO roles (role_id, name, description) VALUES (:r, :n, :d) ON CONFLICT DO NOTHING"),
            {"r": role_id, "n": name, "d": f"Seeded {name} role"},
        )
        for permission in permissions:
            bind.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p) ON CONFLICT DO NOTHING"),
                {"r": role_id, "p": permission},
            )


def downgrade():
    bind = op.get_bind()
    for role_id in ROLES:
        bind.execute(sa.text("DELETE FROM role_permissions WHERE role_id = :r"), {"r": role_id})
        bind.execute(sa.text("DELETE FROM roles WHERE role_id = :r"), {"r": role_id})
    for permission in PERMISSIONS:
        bind.execute(sa.text("DELETE FROM permissions WHERE permission_id = :p"), {"p": permission})
