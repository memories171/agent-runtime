"""tool invocation state machine, lease/fencing and recovery timestamps

Revision ID: 0007_tool_invocation_recovery
Revises: 0006_durable_checkpoint
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_tool_invocation_recovery"
down_revision = "0006_durable_checkpoint"
branch_labels = None
depends_on = None


TOOL_INVOCATION_COLUMNS = [
    ("tool_call_id", sa.String(128), "NULL"),
    ("canonical_arguments_hash", sa.String(64), "''"),
    ("attempt_count", sa.Integer(), "0"),
    ("external_reference", sa.String(256), "NULL"),
    ("result_reference", sa.String(512), "NULL"),
    ("started_at", sa.DateTime(timezone=True), "NULL"),
    ("completed_at", sa.DateTime(timezone=True), "NULL"),
    ("last_error", sa.Text(), "NULL"),
    ("fencing_token", sa.Integer(), "0"),
    ("lease_owner", sa.String(128), "NULL"),
    ("lease_expires_at", sa.DateTime(timezone=True), "NULL"),
    ("reconciliation_status", sa.String(32), "'NONE'"),
    ("reconciliation_note", sa.Text(), "NULL"),
]

WORKER_FAILURE_COLUMNS = [
    ("recovery_started_at", sa.DateTime(timezone=True), "NULL"),
    ("recovery_completed_at", sa.DateTime(timezone=True), "NULL"),
    ("recovery_attempt", sa.Integer(), "0"),
    ("activity_retry_count", sa.Integer(), "0"),
    ("fencing_token", sa.Integer(), "0"),
]


def _add_columns(table: str, columns) -> None:
    pg = sa.dialects.postgresql.dialect()
    for name, column_type, default in columns:
        rendered = column_type.compile(dialect=pg)
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {rendered} DEFAULT {default}"))


def upgrade():
    _add_columns("tool_invocations", TOOL_INVOCATION_COLUMNS)
    _add_columns("worker_failures", WORKER_FAILURE_COLUMNS)
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_invocation_call "
        "ON tool_invocations (tenant_id, run_id, tool_call_id) WHERE tool_call_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_tool_invocation_status ON tool_invocations (tenant_id, status)"
    ))
    op.execute(sa.text(
        "UPDATE tool_invocations SET status = 'CONFIRMED' WHERE status = 'SUCCEEDED' AND status <> 'CONFIRMED'"
    ))
    op.execute(sa.text(
        "UPDATE tool_invocations SET status = 'DISPATCHED' WHERE status = 'PENDING' AND status <> 'DISPATCHED'"
    ))


def downgrade():
    op.execute(sa.text("DROP INDEX IF EXISTS ix_tool_invocation_status"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_tool_invocation_call"))
    for name, _, _ in reversed(WORKER_FAILURE_COLUMNS):
        op.execute(sa.text(f"ALTER TABLE worker_failures DROP COLUMN IF EXISTS {name}"))
    for name, _, _ in reversed(TOOL_INVOCATION_COLUMNS):
        op.execute(sa.text(f"ALTER TABLE tool_invocations DROP COLUMN IF EXISTS {name}"))
