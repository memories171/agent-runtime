"""durable checkpoint envelope and snapshot metadata

Revision ID: 0006_durable_checkpoint
Revises: 0005_run_delete
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_durable_checkpoint"
down_revision = "0005_run_delete"
branch_labels = None
depends_on = None


COLUMNS = [
    ("checkpoint_sequence", sa.Integer(), "0"),
    ("schema_version", sa.String(16), "'1.0'"),
    ("state_digest", sa.String(64), "''"),
    ("blob_sha256", sa.String(64), "''"),
    ("blob_size_bytes", sa.Integer(), "0"),
    ("state_blob_ref", sa.Text(), "''"),
    ("compression_algorithm", sa.String(32), "'zlib'"),
    ("encryption_algorithm", sa.String(32), "'AES-256-GCM'"),
    ("encryption_key_id", sa.String(128), "''"),
    ("envelope_json", sa.JSON(), "'{}'::json"),
    ("persistence_status", sa.String(32), "'CREATED'"),
]


def upgrade():
    for name, column_type, default in COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE run_checkpoints ADD COLUMN IF NOT EXISTS {name} "
                f"{column_type.compile(dialect=sa.dialects.postgresql.dialect())} NOT NULL DEFAULT {default}"
            )
        )
    op.execute(
        sa.text(
            "UPDATE run_checkpoints SET checkpoint_sequence = step WHERE checkpoint_sequence = 0"
        )
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_checkpoint_run_sequence') THEN "
            "ALTER TABLE run_checkpoints ADD CONSTRAINT uq_checkpoint_run_sequence "
            "UNIQUE (run_id, checkpoint_sequence); "
            "END IF; END $$;"
        )
    )
    op.create_index("ix_checkpoint_run_sequence", "run_checkpoints", ["run_id", "checkpoint_sequence"])


def downgrade():
    op.drop_index("ix_checkpoint_run_sequence", table_name="run_checkpoints")
    op.execute(
        sa.text(
            "ALTER TABLE run_checkpoints DROP CONSTRAINT IF EXISTS uq_checkpoint_run_sequence"
        )
    )
    for name, _, _ in reversed(COLUMNS):
        op.execute(sa.text(f"ALTER TABLE run_checkpoints DROP COLUMN IF EXISTS {name}"))
