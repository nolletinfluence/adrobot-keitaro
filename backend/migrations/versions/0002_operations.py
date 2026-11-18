"""Journal remote writes and preserve recoverable publication intent."""

import sqlalchemy as sa
from alembic import op

revision = "0002_operations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "database_target",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("target_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(48), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("stream_id", sa.Integer(), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("detail", sa.String(1024), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_status", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "idempotency_key", name="unique_operation_key"),
        sa.CheckConstraint(
            "status IN ('pending','uncertain','succeeded','failed','conflict')",
            name="valid_operation_status",
        ),
    )
    for column in ("campaign_id", "stream_id", "created_at"):
        op.create_index(f"ix_operations_{column}", "operations", [column])
    op.add_column("streams", sa.Column("pending_operation_id", sa.String(32), nullable=True))
    op.create_index("ix_streams_pending_operation_id", "streams", ["pending_operation_id"])


def downgrade():
    op.drop_index("ix_streams_pending_operation_id", table_name="streams")
    op.drop_column("streams", "pending_operation_id")
    op.drop_table("operations")
    op.drop_table("database_target")
