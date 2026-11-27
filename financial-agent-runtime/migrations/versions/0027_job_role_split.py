"""split business roles into manager / analyst / risk controller

Revision ID: 0027_job_role_split
Revises: 0026_normalize_case_types
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_job_role_split"
down_revision = "0026_normalize_case_types"
branch_labels = None
depends_on = None

NEW_PERMISSIONS = [
    "knowledge:read",
    "knowledge:write",
    "financial:case:review",
    "runs:cancel:tenant",
]

NEW_ROLES = {
    "general_manager": ("General Manager", "业务总经理：治理配置与全局指标，不参与个案审批"),
    "risk_controller": ("Risk Controller", "风控：独立复核案件与高风险操作，不发起业务任务"),
}

ROLE_GRANTS = {
    "tenant_admin": list(NEW_PERMISSIONS),
    "general_manager": [
        "platform:manage", "user:manage", "security:read", "audit:read",
        "financial:audit:read", "runs:read:tenant", "skills:manage",
        "approval:read", "knowledge:read", "knowledge:write",
    ],
    "analyst": [
        "agent:execute", "run:create", "run:read", "run:pause", "run:resume", "run:context:read",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:monitoring:run", "financial:case:create", "financial:case:read",
        "memory:read", "memory:read:team", "memory:write", "memory:write:team",
        "knowledge:read", "tool:execute",
    ],
    "risk_controller": [
        "run:read", "runs:read:tenant", "runs:cancel:tenant",
        "financial:customer:read", "financial:account:read", "financial:transactions:read",
        "financial:case:read", "financial:case:review", "financial:case:approve",
        "approval:read", "approval:approve", "financial:audit:read", "audit:read",
        "knowledge:read", "data:sensitive", "tool:execute",
    ],
}

# Old demo grants made analyst a quasi-auditor. Remove them so analyst stays a
# business execution role and cannot see tenant-wide runs or audit surfaces.
REVOKE_FROM_ANALYST = [
    "runs:read:tenant",
    "runs:manage:tenant",
    "approval:read",
    "security:read",
    "financial:audit:read",
]


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

    for role_id, (name, description) in NEW_ROLES.items():
        bind.execute(
            sa.text(
                "INSERT INTO roles (role_id, name, description) "
                "VALUES (:r, :n, :d) ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "n": name, "d": description},
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

    for permission in REVOKE_FROM_ANALYST:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = 'analyst' AND permission_id = :p"
            ),
            {"p": permission},
        )


def downgrade():
    bind = op.get_bind()

    for permission in REVOKE_FROM_ANALYST:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "VALUES ('analyst', :p) ON CONFLICT DO NOTHING"
            ),
            {"p": permission},
        )

    for role_id, permissions in ROLE_GRANTS.items():
        for permission in permissions:
            bind.execute(
                sa.text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :r AND permission_id = :p"
                ),
                {"r": role_id, "p": permission},
            )

    for role_id in NEW_ROLES:
        bind.execute(sa.text("DELETE FROM user_roles WHERE role_id = :r"), {"r": role_id})
        bind.execute(sa.text("DELETE FROM roles WHERE role_id = :r"), {"r": role_id})

    for permission in NEW_PERMISSIONS:
        bind.execute(sa.text("DELETE FROM permissions WHERE permission_id = :p"), {"p": permission})
