"""Add feedback columns to meal_plans

Revision ID: 002
Revises: 001
Create Date: 2026-04-27

Adds three nullable columns to meal_plans to support the Telegram
feedback loop and memory consolidation system.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meal_plans",
        sa.Column("overall_rating", sa.SmallInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_meal_plans_overall_rating",
        "meal_plans",
        "overall_rating BETWEEN 1 AND 5",
    )
    op.add_column("meal_plans", sa.Column("feedback_notes", sa.Text(), nullable=True))
    # Telegram message ID for the standalone feedback-rating message
    op.add_column(
        "meal_plans",
        sa.Column("feedback_telegram_msg_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meal_plans", "feedback_telegram_msg_id")
    op.drop_column("meal_plans", "feedback_notes")
    op.drop_constraint("ck_meal_plans_overall_rating", "meal_plans")
    op.drop_column("meal_plans", "overall_rating")
