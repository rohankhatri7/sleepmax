"""confound_flag

Revision ID: 0006_confound_flag
Revises: 0005_pattern_metadata
Create Date: 2026-05-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_confound_flag"
down_revision: Union[str, None] = "0005_pattern_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discovered_patterns",
        sa.Column(
            "confound_flag", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "discovered_patterns",
        sa.Column("confounded_with", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovered_patterns", "confounded_with")
    op.drop_column("discovered_patterns", "confound_flag")
