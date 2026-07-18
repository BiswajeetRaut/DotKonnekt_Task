"""add users table, expenses.user_id (backfilled to a demo user)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

from app.auth import hash_password

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo1234"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.add_column("expenses", sa.Column("user_id", sa.Integer(), nullable=True))

    # Backfill: any pre-auth (v1.0) expenses get assigned to a demo account so
    # existing seed data isn't orphaned/lost by this migration.
    conn = op.get_bind()
    result = conn.execute(
        sa.text("INSERT INTO users (email, hashed_password) VALUES (:email, :hashed) RETURNING id"),
        {"email": DEMO_EMAIL, "hashed": hash_password(DEMO_PASSWORD)},
    )
    demo_user_id = result.scalar_one()
    conn.execute(
        sa.text("UPDATE expenses SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": demo_user_id},
    )

    op.alter_column("expenses", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_expenses_user_id", "expenses", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_expenses_user_id", "expenses", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_expenses_user_id", table_name="expenses")
    op.drop_constraint("fk_expenses_user_id", "expenses", type_="foreignkey")
    op.drop_column("expenses", "user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
