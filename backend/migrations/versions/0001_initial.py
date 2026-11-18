"""Persist campaigns, offers and stream draft snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tracking_url", sa.String(2048), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("remote_offers", sa.JSON(), nullable=False),
        sa.Column("published_state", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "stream_offers",
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.CheckConstraint("weight >= 0 AND weight <= 100", name="valid_weight"),
        sa.ForeignKeyConstraint(["stream_id"], ["streams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"]),
        sa.PrimaryKeyConstraint("stream_id", "offer_id"),
    )


def downgrade():
    op.drop_table("stream_offers")
    op.drop_table("streams")
    op.drop_table("offers")
    op.drop_table("campaigns")
