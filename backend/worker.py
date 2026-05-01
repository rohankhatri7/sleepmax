"""Celery application for background processing.

Run with: celery -A backend.worker worker --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab

from backend.config import settings

app = Celery(
    "sleepmax",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["backend.tasks"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30-minute hard limit
    result_expires=24 * 3600,  # keep status for 24h
)

app.conf.beat_schedule = {
    "nightly-context-sync": {
        "task": "backend.tasks.sync_context",
        "schedule": crontab(hour=3, minute=15),
    },
}
