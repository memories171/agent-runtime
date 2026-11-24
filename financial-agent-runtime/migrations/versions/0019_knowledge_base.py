"""knowledge base documents and chunks

Revision ID: 0019_knowledge
Revises: 0018_chat
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_knowledge"
down_revision = "0018_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("source", sa.String(512), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(32), nullable=False, server_default="markdown"),
        sa.Column("char_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_doc_tenant_created", "knowledge_documents", ["tenant_id", "created_at"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("heading", sa.String(256), nullable=False, server_default=""),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("vector", sa.JSON, nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_chunk_tenant_doc", "knowledge_chunks", ["tenant_id", "document_id"])
    op.create_index("ix_kb_chunk_tenant_created", "knowledge_chunks", ["tenant_id", "created_at"])

    for table in ("knowledge_documents", "knowledge_chunks"):
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
    for table in ("knowledge_documents", "knowledge_chunks"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
