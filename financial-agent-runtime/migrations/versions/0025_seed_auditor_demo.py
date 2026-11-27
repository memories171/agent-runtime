"""seed auditor demo identity (moved out of initdb)

PostgreSQL initdb scripts run before Alembic creates the identity tables, so
the demo tenant/user can only be inserted after the schema migration.

Revision ID: 0025_seed_auditor_demo
Revises: 0024_knowledge_document_raw_content
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_seed_auditor_demo"
down_revision = "0024_knowledge_document_raw_content"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text("""
        INSERT INTO tenants (tenant_id, display_name, status)
        VALUES ('tenant-b', 'Tenant B', 'active')
        ON CONFLICT DO NOTHING
    """))
    bind.execute(sa.text("""
        INSERT INTO organizations (organization_id, tenant_id, name, status)
        VALUES ('org-b', 'tenant-b', 'Org B', 'active')
        ON CONFLICT DO NOTHING
    """))
    bind.execute(sa.text("""
        INSERT INTO users (user_id, display_name, email, status)
        VALUES ('auditor-01', 'Auditor User', 'auditor-01@example.com', 'active')
        ON CONFLICT DO NOTHING
    """))
    bind.execute(sa.text("""
        INSERT INTO tenant_memberships (membership_id, tenant_id, user_id, organization_id, status)
        VALUES (gen_random_uuid(), 'tenant-b', 'auditor-01', 'org-b', 'active')
        ON CONFLICT (tenant_id, user_id) DO UPDATE SET status = 'active', organization_id = 'org-b'
    """))
    bind.execute(sa.text("""
        INSERT INTO user_roles (user_role_id, tenant_id, user_id, role_id, granted_by)
        VALUES (gen_random_uuid(), 'tenant-b', 'auditor-01', 'auditor', 'seed')
        ON CONFLICT (tenant_id, user_id, role_id) DO NOTHING
    """))


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM user_roles WHERE tenant_id = 'tenant-b' AND user_id = 'auditor-01'"))
    bind.execute(sa.text("DELETE FROM tenant_memberships WHERE tenant_id = 'tenant-b' AND user_id = 'auditor-01'"))
    bind.execute(sa.text("DELETE FROM users WHERE user_id = 'auditor-01'"))
    bind.execute(sa.text("DELETE FROM organizations WHERE organization_id = 'org-b'"))
    bind.execute(sa.text("DELETE FROM tenants WHERE tenant_id = 'tenant-b'"))
