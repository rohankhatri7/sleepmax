"""pattern_metadata

Revision ID: 0005_pattern_metadata
Revises: 0004_insights
Create Date: 2026-04-30 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_pattern_metadata"
down_revision: Union[str, None] = "0004_insights"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discovered_patterns",
        sa.Column("p_value", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "discovered_patterns",
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "discovered_patterns",
        sa.Column("threshold", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "discovered_patterns",
        sa.Column(
            "confidence_label",
            sa.String(length=20),
            nullable=False,
            server_default="emerging",
        ),
    )


def downgrade() -> None:
    op.drop_column("discovered_patterns", "confidence_label")
    op.drop_column("discovered_patterns", "threshold")
    op.drop_column("discovered_patterns", "lag_days")
    op.drop_column("discovered_patterns", "p_value")
