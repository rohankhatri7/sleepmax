"""Pydantic request/response models for the API."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


# --- Upload ---

class UploadResponse(BaseModel):
    """Result of a synchronous upload (used by tasks running inline)."""

    status: str
    records_processed: int
    sessions_created: int
    sessions_updated: int = 0
    errors: list[str]


class UploadEnqueueResponse(BaseModel):
    """202 response when an upload is queued for background processing."""

    status: str
    job_id: str
    status_url: str


class UploadStatusResponse(BaseModel):
    """Status of a background parse job."""

    state: str  # pending | started | progress | success | failure | failed
    meta: dict | None = None
    result: dict | None = None
    error: str | None = None


# --- Sleep Records ---

class SleepRecordOut(BaseModel):
    id: UUID
    source: str
    date: datetime
    sleep_start: datetime
    sleep_end: datetime
    tz_offset_minutes: int
    total_duration_min: float
    time_in_bed_min: float
    deep_min: float
    light_min: float
    rem_min: float
    awake_min: float
    efficiency: float | None
    avg_hr: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SleepRecordList(BaseModel):
    records: list[SleepRecordOut]
    count: int


# --- Daily Context ---

class DailyContextOut(BaseModel):
    id: UUID
    date: datetime
    temp_high_c: float | None
    temp_low_c: float | None
    humidity_pct: float | None
    pressure_hpa: float | None
    precipitation_mm: float | None
    meeting_count: int | None
    meeting_hours: float | None
    first_meeting_time: str | None
    last_meeting_time: str | None
    back_to_back_count: int | None
    exercise_min: float | None
    exercise_type: str | None
    exercise_intensity: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Weather fetch request ---

class WeatherRequest(BaseModel):
    date: date
    latitude: float
    longitude: float


# --- Insights (Agent 4) ---

class InsightGenerateRequest(BaseModel):
    for_date: date | None = None
    lookback_days: int = 14


class InsightOut(BaseModel):
    id: UUID
    generated_for_date: date
    weekly_digest: str
    insights: list[str]
    patterns_used: list[str]
    model_name: str
    created_at: datetime

    model_config = {"from_attributes": True}
