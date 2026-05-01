"""Context orchestrator — fans out enabled adapters and upserts the merged result.

Discovers which `ContextAdapter`s are configured for the current environment,
runs them concurrently, merges the partial dicts each one returns, and writes
one `DailyContext` row. Adapter failures are logged and do not abort the run;
the orchestrator only writes when at least one adapter produced data.

Don't overwrite a populated field with NULL on a re-run — the upload pipeline
also writes into `daily_context`, and partial adapter coverage on a later
orchestrator run shouldn't erase its contributions.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.context.base import ContextAdapter
from backend.agents.context.calendar import CalendarAdapter
from backend.agents.context.exercise import ExerciseAdapter
from backend.agents.context.weather import WeatherAdapter
from backend.agents.ingestion.base import WorkoutSession
from backend.config import settings
from backend.db.models import DailyContext, UserToken
from backend.services.oauth import PROVIDER_GOOGLE_CALENDAR

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    date: date
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    fields_written: list[str] = field(default_factory=list)


async def _calendar_token_present(session: AsyncSession) -> bool:
    stmt = select(UserToken.provider).where(UserToken.provider == PROVIDER_GOOGLE_CALENDAR)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _discover_adapters(
    session: AsyncSession,
    *,
    lat: float | None,
    lon: float | None,
    workouts: list[WorkoutSession] | None,
) -> tuple[list[tuple[ContextAdapter, dict[str, Any]]], list[str]]:
    """Return [(adapter, kwargs)] for adapters with config present, plus skipped names."""
    enabled: list[tuple[ContextAdapter, dict[str, Any]]] = []
    skipped: list[str] = []

    if lat is not None and lon is not None:
        enabled.append((WeatherAdapter(), {"latitude": lat, "longitude": lon}))
    else:
        skipped.append("weather")

    if await _calendar_token_present(session):
        enabled.append((CalendarAdapter(), {"session": session}))
    else:
        skipped.append("calendar")

    if workouts:
        enabled.append((ExerciseAdapter(), {"workouts": workouts}))
    else:
        skipped.append("exercise")

    return enabled, skipped


async def _run_adapter(
    adapter: ContextAdapter, target_date: date, kwargs: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.fetch(target_date, **kwargs)


def _merge(
    partials: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any], list[str]]:
    """Combine partial dicts into one, dropping None values. Returns (merged, ran)."""
    merged: dict[str, Any] = {}
    ran: list[str] = []
    for name, partial in partials:
        non_null = {k: v for k, v in partial.items() if v is not None}
        if not non_null:
            continue
        for k, v in non_null.items():
            if k in merged and merged[k] != v:
                logger.warning(
                    "Adapter %s overwriting field %s: %r -> %r", name, k, merged[k], v
                )
            merged[k] = v
        ran.append(name)
    return merged, ran


async def _upsert(
    session: AsyncSession, target_date: date, fields: dict[str, Any]
) -> list[str]:
    """Upsert non-null fields into the DailyContext row for target_date.

    Existing non-null columns are preserved (we only setattr keys present in
    `fields`, all of which are already non-null after _merge).
    """
    date_dt = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    stmt = select(DailyContext).where(DailyContext.date == date_dt)  # type: ignore[arg-type]
    existing = (await session.execute(stmt)).scalar_one_or_none()

    written: list[str] = []
    if existing is None:
        row = DailyContext(date=date_dt)  # type: ignore[arg-type]
        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
                written.append(k)
        session.add(row)
    else:
        for k, v in fields.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
                written.append(k)

    await session.commit()
    return written


async def orchestrate(
    target_date: date,
    session: AsyncSession,
    *,
    lat: float | None = None,
    lon: float | None = None,
    workouts: list[WorkoutSession] | None = None,
    adapters: list[tuple[ContextAdapter, dict[str, Any]]] | None = None,
) -> OrchestratorResult:
    """Run all configured context adapters for `target_date` and upsert the merged result.

    Args:
        target_date: date to fetch context for.
        session: AsyncSession used for both calendar credential loading and persistence.
        lat / lon: weather location. Falls back to skipping weather if either is None.
        workouts: pre-parsed workouts for the date (exercise adapter is skipped without).
        adapters: test-only override — bypasses configuration discovery.

    Failures from any individual adapter are caught, logged, and reported in the
    result without aborting the rest of the run. No row is written if every
    adapter fails or is skipped.
    """
    result = OrchestratorResult(date=target_date)

    if adapters is None:
        adapters, result.skipped = await _discover_adapters(
            session, lat=lat, lon=lon, workouts=workouts
        )

    if not adapters:
        return result

    coros = [_run_adapter(a, target_date, kw) for a, kw in adapters]
    outcomes = await asyncio.gather(*coros, return_exceptions=True)

    successes: list[tuple[str, dict[str, Any]]] = []
    for (adapter, _kw), outcome in zip(adapters, outcomes):
        name = adapter.adapter_name
        if isinstance(outcome, BaseException):
            logger.exception(
                "Context adapter %s failed for %s: %s", name, target_date, outcome,
                exc_info=outcome,
            )
            result.failed[name] = str(outcome)
        else:
            successes.append((name, outcome))

    merged, ran = _merge(successes)
    result.ran = ran

    if not merged:
        return result

    result.fields_written = await _upsert(session, target_date, merged)
    return result


def default_lat_lon() -> tuple[float, float]:
    """Configured default location for weather lookups."""
    return settings.default_latitude, settings.default_longitude
