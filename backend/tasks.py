"""Celery tasks for background ingestion of wearable data.

These tasks run in Celery workers (synchronous). They use a sync SQLAlchemy
engine derived from `settings.database_url_sync` to keep the worker code
straightforward — no async event-loop juggling inside the task body.
"""

import logging
import os
from datetime import datetime, time, timezone
from pathlib import Path

from sqlalchemy import create_engine, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from backend.agents.context.exercise import aggregate_workouts_by_day
from backend.agents.ingestion.apple_health import AppleHealthParser
from backend.agents.ingestion.base import ParseResult
from backend.config import settings
from backend.db.models import DailyContext, SleepRecord
from backend.worker import app

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def _get_session() -> Session:
    """Lazy-init a sync session factory bound to the worker's DB engine."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = create_engine(settings.database_url_sync, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal()


SLEEP_UPDATABLE_COLS = (
    "date", "tz_offset_minutes", "total_duration_min", "time_in_bed_min",
    "deep_min", "light_min", "rem_min", "awake_min", "efficiency", "avg_hr",
    "updated_at",
)


def _persist(session: Session, parsed: ParseResult) -> dict:
    """Upsert sleep sessions and exercise context. Returns counts dict."""
    inserted = updated = 0

    if parsed.sessions:
        now = datetime.now(timezone.utc)
        rows = [{
            "source": s.source, "date": s.date,
            "sleep_start": s.sleep_start, "sleep_end": s.sleep_end,
            "tz_offset_minutes": s.tz_offset_minutes,
            "total_duration_min": s.total_duration_min,
            "time_in_bed_min": s.time_in_bed_min,
            "deep_min": s.deep_min, "light_min": s.light_min,
            "rem_min": s.rem_min, "awake_min": s.awake_min,
            "efficiency": s.efficiency, "avg_hr": s.avg_hr,
            "created_at": now, "updated_at": now,
        } for s in parsed.sessions]

        stmt = pg_insert(SleepRecord).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sleep_record_source_window",
            set_={col: stmt.excluded[col] for col in SLEEP_UPDATABLE_COLS},
        ).returning(literal_column("xmax = 0").label("was_inserted"))
        flags = list(session.execute(stmt).scalars())
        inserted = sum(1 for f in flags if f)
        updated = sum(1 for f in flags if not f)

    rollup = aggregate_workouts_by_day(parsed.workouts)
    for day, fields in rollup.items():
        date_dt = datetime.combine(day, time.min, tzinfo=timezone.utc)
        existing = session.execute(
            select(DailyContext).where(DailyContext.date == date_dt)
        ).scalar_one_or_none()
        if existing is None:
            session.add(DailyContext(date=date_dt, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)

    session.commit()
    return {
        "records_processed": parsed.records_processed,
        "sessions_inserted": inserted,
        "sessions_updated": updated,
        "workout_days": len(rollup),
    }


@app.task(bind=True, name="backend.tasks.parse_apple_health")
def parse_apple_health(self, file_path: str) -> dict:
    """Parse an Apple Health export and persist results. Cleans up the file when done."""
    path = Path(file_path)
    try:
        self.update_state(state="PROGRESS", meta={"phase": "parsing"})
        parser = AppleHealthParser()
        parsed = parser.parse(path)

        self.update_state(state="PROGRESS", meta={
            "phase": "persisting",
            "records_processed": parsed.records_processed,
            "sessions": len(parsed.sessions),
            "workouts": len(parsed.workouts),
        })

        with _get_session() as session:
            counts = _persist(session, parsed)

        return {"status": "ok", **counts, "errors": parsed.errors}
    except Exception as e:
        logger.exception("parse_apple_health failed for %s", file_path)
        raise
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
