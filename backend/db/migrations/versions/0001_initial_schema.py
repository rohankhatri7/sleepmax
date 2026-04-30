"""initial_schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-30 01:46:22.449897
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_context",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_high_c", sa.Float(), nullable=True),
        sa.Column("temp_low_c", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("pressure_hpa", sa.Float(), nullable=True),
        sa.Column("precipitation_mm", sa.Float(), nullable=True),
        sa.Column("meeting_count", sa.Integer(), nullable=True),
        sa.Column("meeting_hours", sa.Float(), nullable=True),
        sa.Column("first_meeting_time", sa.String(length=5), nullable=True),
        sa.Column("last_meeting_time", sa.String(length=5), nullable=True),
        sa.Column("back_to_back_count", sa.Integer(), nullable=True),
        sa.Column("exercise_min", sa.Float(), nullable=True),
        sa.Column("exercise_type", sa.String(length=100), nullable=True),
        sa.Column("exercise_intensity", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date"),
    )
    op.create_table(
        "discovered_patterns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pattern_type", sa.String(length=50), nullable=False),
        sa.Column("context_field", sa.String(length=100), nullable=False),
        sa.Column("sleep_metric", sa.String(length=100), nullable=False),
        sa.Column("correlation_strength", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sleep_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sleep_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sleep_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tz_offset_minutes", sa.Integer(), nullable=False),
        sa.Column("total_duration_min", sa.Float(), nullable=False),
        sa.Column("time_in_bed_min", sa.Float(), nullable=False),
        sa.Column("deep_min", sa.Float(), nullable=False),
        sa.Column("light_min", sa.Float(), nullable=False),
        sa.Column("rem_min", sa.Float(), nullable=False),
        sa.Column("awake_min", sa.Float(), nullable=False),
        sa.Column("efficiency", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sleep_records")
    op.drop_table("discovered_patterns")
    op.drop_table("daily_context")
