"""Celery tasks for background ingestion of wearable data.

These tasks run in Celery workers (synchronous). They use a sync SQLAlchemy
engine derived from `settings.database_url_sync` to keep the worker code
straightforward — no async event-loop juggling inside the task body.
"""

import asyncio
import dataclasses
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.agents.context.exercise import aggregate_workouts_by_day
from backend.agents.context.orchestrator import orchestrate
from backend.agents.discovery.discover import (
    CONTEXT_FIELDS,
    SLEEP_METRICS,
    discover_patterns,
)
from backend.agents.discovery.persist import persist_patterns
from backend.agents.ingestion.apple_health import AppleHealthParser
from backend.agents.ingestion.base import ParseResult
from backend.config import settings
from backend.db.models import DailyContext, DiscoveredPattern, SleepRecord
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


_async_engine = None
_AsyncSessionLocal = None


def _get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_engine, _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _async_engine = create_async_engine(settings.database_url, future=True)
        _AsyncSessionLocal = async_sessionmaker(
            _async_engine, class_=AsyncSession, expire_on_commit=False
        )
    return _AsyncSessionLocal


async def _run_orchestrator(target: date) -> dict:
    factory = _get_async_session_factory()
    async with factory() as session:
        result = await orchestrate(
            target,
            session,
            lat=settings.default_latitude,
            lon=settings.default_longitude,
        )
    payload = dataclasses.asdict(result)
    payload["date"] = result.date.isoformat()
    return payload


@app.task(name="backend.tasks.sync_context")
def sync_context(date_iso: str | None = None) -> dict:
    """Run the context orchestrator for a date (default: yesterday UTC).

    Chains `run_discovery` afterwards so newly-merged context fields immediately
    feed the next pattern run.
    """
    target = (
        date.fromisoformat(date_iso)
        if date_iso
        else (datetime.now(timezone.utc).date() - timedelta(days=1))
    )
    result = asyncio.run(_run_orchestrator(target))
    run_discovery.delay()
    return result


def _load_sleep_df(session: Session) -> pd.DataFrame:
    rows = session.execute(select(SleepRecord)).scalars().all()
    if not rows:
        return pd.DataFrame(columns=("date", *SLEEP_METRICS))
    data = [
        {"date": r.date, **{m: getattr(r, m) for m in SLEEP_METRICS}}
        for r in rows
    ]
    return pd.DataFrame(data)


def _load_context_df(session: Session) -> pd.DataFrame:
    rows = session.execute(select(DailyContext)).scalars().all()
    if not rows:
        return pd.DataFrame(columns=("date", *CONTEXT_FIELDS))
    data = [
        {"date": r.date, **{f: getattr(r, f) for f in CONTEXT_FIELDS}}
        for r in rows
    ]
    return pd.DataFrame(data)


@app.task(name="backend.tasks.run_discovery")
def run_discovery() -> dict:
    """Recompute and persist `discovered_patterns` from the full sleep+context history."""
    with _get_session() as session:
        sleep_df = _load_sleep_df(session)
        context_df = _load_context_df(session)
        patterns = discover_patterns(sleep_df, context_df)
        n = persist_patterns(patterns, session)
    flagged = sum(1 for p in patterns if p.confound_flag)
    return {
        "status": "ok",
        "patterns_persisted": n,
        "patterns_flagged": flagged,
        "sleep_rows": len(sleep_df),
        "context_rows": len(context_df),
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
