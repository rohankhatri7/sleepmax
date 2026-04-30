"""insights

Revision ID: 0004_insights
Revises: 0003_sleep_record_unique
Create Date: 2026-04-30 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_insights"
down_revision: Union[str, None] = "0003_sleep_record_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("generated_for_date", sa.Date(), nullable=False),
        sa.Column("weekly_digest", sa.Text(), nullable=False),
        sa.Column("insights_json", sa.Text(), nullable=False),
        sa.Column("patterns_used", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_insights_for_date", "insights", ["generated_for_date"])


def downgrade() -> None:
    op.drop_index("ix_insights_for_date", table_name="insights")
    op.drop_table("insights")
