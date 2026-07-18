"""add categories table; replace expenses.category (text) with category_id (FK)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_categories_user_id_name"),
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"])

    conn = op.get_bind()

    # Backfill: one categories row per distinct (user_id, category text) pair
    # that already exists in expenses.
    conn.execute(
        sa.text(
            """
            INSERT INTO categories (user_id, name)
            SELECT DISTINCT user_id, category FROM expenses
            """
        )
    )

    op.add_column("expenses", sa.Column("category_id", sa.Integer(), nullable=True))
    conn.execute(
        sa.text(
            """
            UPDATE expenses
            SET category_id = categories.id
            FROM categories
            WHERE categories.user_id = expenses.user_id AND categories.name = expenses.category
            """
        )
    )

    op.alter_column("expenses", "category_id", nullable=False)
    op.create_foreign_key(
        "fk_expenses_category_id", "expenses", "categories", ["category_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_expenses_category_id", "expenses", ["category_id"])

    op.drop_index("ix_expenses_category", table_name="expenses")
    op.drop_column("expenses", "category")


def downgrade() -> None:
    op.add_column("expenses", sa.Column("category", sa.String(64), nullable=True))
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE expenses
            SET category = categories.name
            FROM categories
            WHERE categories.id = expenses.category_id
            """
        )
    )
    op.alter_column("expenses", "category", nullable=False)
    op.create_index("ix_expenses_category", "expenses", ["category"])

    op.drop_index("ix_expenses_category_id", table_name="expenses")
    op.drop_constraint("fk_expenses_category_id", "expenses", type_="foreignkey")
    op.drop_column("expenses", "category_id")

    op.drop_index("ix_categories_user_id", table_name="categories")
    op.drop_table("categories")
