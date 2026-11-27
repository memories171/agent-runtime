"""seed demo users for manager / analyst / risk role split

Revision ID: 0028_seed_job_role_users
Revises: 0027_job_role_split
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_seed_job_role_users"
down_revision = "0027_job_role_split"
branch_labels = None
depends_on = None

DEMO_USERS = [
    ("manager-01", "General Manager", "manager-01@example.com", "general_manager"),
    ("analyst-01", "Analyst User", "analyst-01@example.com", "analyst"),
    ("risk-01", "Risk Controller", "risk-01@example.com", "risk_controller"),
]


def upgrade():
    bind = op.get_bind()
    for user_id, display_name, email, role_id in DEMO_USERS:
        bind.execute(
            sa.text(
                "INSERT INTO users (user_id, display_name, email, status) "
                "VALUES (:u, :d, :e, 'active') ON CONFLICT DO NOTHING"
            ),
            {"u": user_id, "d": display_name, "e": email},
        )
        bind.execute(
            sa.text(
                "INSERT INTO tenant_memberships "
                "(membership_id, tenant_id, user_id, organization_id, status, role_assignments) "
                "VALUES (gen_random_uuid(), 'demo-bank', :u, 'demo-org', 'active', '[]'::jsonb) "
                "ON CONFLICT (tenant_id, user_id) DO UPDATE SET status = 'active', "
                "organization_id = 'demo-org'"
            ),
            {"u": user_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO user_roles (user_role_id, tenant_id, user_id, role_id, granted_by) "
                "VALUES (gen_random_uuid(), 'demo-bank', :u, :r, 'seed') "
                "ON CONFLICT (tenant_id, user_id, role_id) DO NOTHING"
            ),
            {"u": user_id, "r": role_id},
        )


def downgrade():
    bind = op.get_bind()
    for user_id, _display, _email, role_id in DEMO_USERS:
        bind.execute(
            sa.text(
                "DELETE FROM user_roles "
                "WHERE tenant_id = 'demo-bank' AND user_id = :u AND role_id = :r"
            ),
            {"u": user_id, "r": role_id},
        )
    for user_id, _display, _email, _role in DEMO_USERS:
        bind.execute(
            sa.text(
                "DELETE FROM tenant_memberships "
                "WHERE tenant_id = 'demo-bank' AND user_id = :u"
            ),
            {"u": user_id},
        )
        bind.execute(sa.text("DELETE FROM users WHERE user_id = :u"), {"u": user_id})
