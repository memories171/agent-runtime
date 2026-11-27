"""normalize seeded review case types

Earlier seed data used display-style case types that are not part of the
runtime enum, which allowed the resource-grant check to be skipped. Normalize
existing rows and add a CHECK constraint so unknown values cannot be inserted.

Revision ID: 0026_normalize_case_types
Revises: 0025_seed_auditor_demo
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_normalize_case_types"
down_revision = "0025_seed_auditor_demo"
branch_labels = None
depends_on = None

_MAPPING = {
    "TRANSACTION_MONITORING": "AML",
    "KYC_REVIEW": "KYC",
    "RECONCILIATION": "TRANSACTION_REVIEW",
}


def upgrade():
    bind = op.get_bind()
    for old, new in _MAPPING.items():
        bind.execute(
            sa.text("UPDATE financial_review_cases SET case_type = :new WHERE case_type = :old"),
            {"new": new, "old": old},
        )
    op.create_check_constraint(
        "ck_financial_review_cases_case_type",
        "financial_review_cases",
        "case_type IN ('KYC', 'AML', 'TRANSACTION_REVIEW')",
    )


def downgrade():
    op.drop_check_constraint("ck_financial_review_cases_case_type", "financial_review_cases")
