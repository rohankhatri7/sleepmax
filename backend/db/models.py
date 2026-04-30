"""SQLAlchemy models matching the unified schemas from ARCHITECTURE.md."""

import uuid
from datetime import datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SleepRecord(Base):
    """One row per sleep session (night). The unified sleep schema."""

    __tablename__ = "sleep_records"
    __table_args__ = (
        UniqueConstraint(
            "source", "sleep_start", "sleep_end",
            name="uq_sleep_record_source_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # apple_health, fitbit, oura
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # the night of
    sleep_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sleep_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Duration breakdown in minutes
    total_duration_min: Mapped[float] = mapped_column(Float, nullable=False)
    time_in_bed_min: Mapped[float] = mapped_column(Float, nullable=False)
    deep_min: Mapped[float] = mapped_column(Float, default=0.0)
    light_min: Mapped[float] = mapped_column(Float, default=0.0)
    rem_min: Mapped[float] = mapped_column(Float, default=0.0)
    awake_min: Mapped[float] = mapped_column(Float, default=0.0)

    # Computed metrics
    efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)  # total_duration / time_in_bed
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg resting HR during sleep

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DailyContext(Base):
    """One row per day. The context vector combining all environmental/behavioral signals."""

    __tablename__ = "daily_context"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True)

    # Weather
    temp_high_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_low_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Calendar
    meeting_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meeting_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_meeting_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "09:00"
    last_meeting_time: Mapped[str | None] = mapped_column(String(5), nullable=True)   # "17:30"
    back_to_back_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Exercise (from wearable data)
    exercise_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    exercise_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exercise_intensity: Mapped[str | None] = mapped_column(String(20), nullable=True)  # low, moderate, high

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UserToken(Base):
    """Encrypted OAuth credentials for third-party providers (Google Calendar, etc.).

    Single-user model: `provider` is the primary key. When multi-tenancy is
    introduced, add a `user_id` column and make (user_id, provider) composite.
    """

    __tablename__ = "user_tokens"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_uri: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)  # space-separated
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DiscoveredPattern(Base):
    """One row per discovered correlation pattern. Populated by Agent 3."""

    __tablename__ = "discovered_patterns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pattern_type: Mapped[str] = mapped_column(String(50), nullable=False)  # correlation | binned
    context_field: Mapped[str] = mapped_column(String(100), nullable=False)
    sleep_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_strength: Mapped[float] = mapped_column(Float, nullable=False)  # signed effect
    confidence: Mapped[float] = mapped_column(Float, nullable=False)  # numeric score (1 - p_corrected)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # Added by 0005_pattern_metadata
    p_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lag_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence_label: Mapped[str] = mapped_column(String(20), nullable=False, default="emerging")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class Insight(Base):
    """One row per insight-generation run (Agent 4 output)."""

    __tablename__ = "insights"
    __table_args__ = (
        Index("ix_insights_for_date", "generated_for_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generated_for_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    weekly_digest: Mapped[str] = mapped_column(Text, nullable=False)
    insights_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list[str]
    patterns_used: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list[str of pattern IDs]
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
