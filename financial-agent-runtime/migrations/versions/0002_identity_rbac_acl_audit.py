"""identity rbac acl audit tables

Revision ID: 0002_identity
Revises: 0001_baseline
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_identity"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_org_tenant_name"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("email", sa.String(256), nullable=True, unique=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("membership_id", UUID, primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("role_assignments", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )
    op.create_table(
        "roles",
        sa.Column("role_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_table(
        "permissions",
        sa.Column("permission_id", sa.String(128), primary_key=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(128), sa.ForeignKey("roles.role_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", sa.String(128), sa.ForeignKey("permissions.permission_id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_role_id", UUID, primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.String(128), sa.ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "user_id", "role_id", name="uq_user_role_tenant"),
    )
    op.create_table(
        "resource_acls",
        sa.Column("acl_id", UUID, primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("granted_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "user_id", "resource_type", "resource_id", "permission", name="uq_resource_acl"),
    )
    op.create_table(
        "auth_audit_logs",
        sa.Column("audit_id", UUID, primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("actor_user_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    for name in ["auth_audit_logs", "resource_acls", "user_roles", "role_permissions", "permissions",
                 "roles", "tenant_memberships", "users", "organizations", "tenants"]:
        op.drop_table(name)
