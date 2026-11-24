"""add review workflow fields and case review permission

Revision ID: 0023_case_review_actions
Revises: 0022_benchmark_model_compare
"""

from alembic import op
import json
import sqlalchemy as sa

revision = "0023_case_review_actions"
down_revision = "0022_benchmark_model_compare"
branch_labels = None
depends_on = None

PERMISSION_ID = "financial:case:review"
ROLE_IDS = ["tenant_admin", "analyst", "compliance_officer"]

COLUMNS = [
    ("reviewer", sa.Column("reviewer", sa.String(128), nullable=True)),
    ("review_decision", sa.Column("review_decision", sa.String(32), nullable=True)),
    ("review_reason", sa.Column("review_reason", sa.Text(), nullable=True)),
    ("reviewed_at", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)),
    ("review_history", sa.Column("review_history", sa.JSON(), nullable=False, server_default="[]")),
]

SEED_CASES = [
    {
        "case_id": "11111111-1111-4111-8111-111111111001",
        "case_type": "TRANSACTION_MONITORING",
        "subject_id": "ACC-DEMO-001",
        "rationale": "短时高频借记命中演示规则，建议结合客户背景人工核查。",
        "evidence_transaction_ids": ["TX-DEMO-001", "TX-DEMO-002"],
        "status": "OPEN",
        "created_by": "analyst-01",
        "idempotency_key": "DEMO-CASE-1001",
    },
    {
        "case_id": "22222222-2222-4222-8222-222222222002",
        "case_type": "KYC_REVIEW",
        "subject_id": "CUS-DEMO-002",
        "rationale": "尽调材料版本过期，需要合规人员确认完整性。",
        "evidence_transaction_ids": [],
        "status": "OPEN",
        "created_by": "analyst-01",
        "idempotency_key": "DEMO-CASE-1002",
    },
    {
        "case_id": "33333333-3333-4333-8333-333333333003",
        "case_type": "RECONCILIATION",
        "subject_id": "ACC-DEMO-002",
        "rationale": "演示对账差异待人工确认。",
        "evidence_transaction_ids": ["TX-DEMO-009"],
        "status": "OPEN",
        "created_by": "analyst-01",
        "idempotency_key": "DEMO-CASE-1003",
    },
]


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO permissions (permission_id, description) "
            "VALUES (:p, :d) ON CONFLICT DO NOTHING"
        ),
        {"p": PERMISSION_ID, "d": "复核 KYC / AML 案件"},
    )
    for role_id in ROLE_IDS:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "VALUES (:r, :p) ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "p": PERMISSION_ID},
        )
    for name, column in COLUMNS:
        op.add_column("financial_review_cases", column)

    for case in SEED_CASES:
        bind.execute(
            sa.text("""
                INSERT INTO financial_review_cases (
                    case_id, tenant_id, case_type, subject_id, rationale,
                    evidence_transaction_ids, status, created_by, idempotency_key
                ) VALUES (
                    :case_id, :tenant_id, :case_type, :subject_id, :rationale,
                    :evidence_transaction_ids, :status, :created_by, :idempotency_key
                ) ON CONFLICT DO NOTHING
            """),
            {
                **case,
                "tenant_id": "demo-bank",
                "evidence_transaction_ids": json.dumps(case["evidence_transaction_ids"]),
            },
        )


def downgrade():
    bind = op.get_bind()
    for case in SEED_CASES:
        bind.execute(
            sa.text("DELETE FROM financial_review_cases WHERE case_id = :case_id"),
            {"case_id": case["case_id"]},
        )
    for name, _column in reversed(COLUMNS):
        op.drop_column("financial_review_cases", name)
    bind = op.get_bind()
    for role_id in ROLE_IDS:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE role_id = :r AND permission_id = :p"
            ),
            {"r": role_id, "p": PERMISSION_ID},
        )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE permission_id = :p"),
        {"p": PERMISSION_ID},
    )
