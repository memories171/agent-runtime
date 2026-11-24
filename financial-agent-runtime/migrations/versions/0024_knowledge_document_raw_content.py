"""add raw_content to knowledge documents

Revision ID: 0024_knowledge_document_raw_content
Revises: 0023_case_review_actions
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_knowledge_document_raw_content"
down_revision = "0023_case_review_actions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "knowledge_documents",
        sa.Column("raw_content", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("knowledge_documents", "raw_content")
