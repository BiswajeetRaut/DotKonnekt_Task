"""add alerts.source to distinguish rule-based vs LLM-based alerts

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("source", sa.String(16), nullable=False, server_default="rule"),
    )


def downgrade() -> None:
    op.drop_column("alerts", "source")
