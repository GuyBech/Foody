"""Add current_state column to users for conversational state tracking

Revision ID: 004
Revises: 003
Create Date: 2026-04-30

The interactive evening flow needs a per-user conversational state so the
free-text follow-up after tapping "✍️ יש שינויים אחרים" can be associated
with the right intent. NULL means idle; "WAITING_FOR_CHANGES" means the
next text message should be treated as a meal-plan override.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("current_state", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "current_state")
