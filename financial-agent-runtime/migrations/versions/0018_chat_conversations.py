"""chat conversations and messages

Revision ID: 0018_chat
Revises: 0017_benchmark
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_chat"
down_revision = "0017_benchmark"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_conversations",
        sa.Column("conversation_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False, server_default="新对话"),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_conv_tenant_updated", "chat_conversations", ["tenant_id", "updated_at"])

    op.create_table(
        "chat_messages",
        sa.Column("message_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("chat_conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chat_msg_conv_created", "chat_messages", ["conversation_id", "created_at"])

    for table in ("chat_conversations", "chat_messages"):
        policy = f"tenant_isolation_{table}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {policy} ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)
                OR current_setting('app.system_context', true) IN ('outbox_dispatcher', 'identity_resolver')
            )
        """)


def downgrade():
    for table in ("chat_conversations", "chat_messages"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
