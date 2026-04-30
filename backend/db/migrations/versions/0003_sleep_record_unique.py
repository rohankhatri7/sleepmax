"""sleep_record_unique

Revision ID: 8498665c41da
Revises: 0002_user_token
Create Date: 2026-04-30 01:56:33.827342
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_sleep_record_unique"
down_revision: Union[str, None] = "0002_user_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sleep_record_source_window",
        "sleep_records",
        ["source", "sleep_start", "sleep_end"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sleep_record_source_window",
        "sleep_records",
        type_="unique",
    )
