"""Fitbit JSON sleep export parser.

Fitbit's user data export ("Settings → Data Export → Export Your Account
Archive") produces per-day files named `sleep-YYYY-MM-DD.json`. Each file
contains an array of sleep entries — sometimes wrapped in a top-level
`{"sleep": [...]}` object, sometimes a bare list. This parser accepts both.

Two stage formats coexist in real exports:
- **Modern (`type=stages`)**: explicit `deep`, `light`, `rem`, `wake` levels,
  available on devices that support sleep stages (post-2017 Fitbit Charge 2+).
- **Legacy (`type=classic`)**: only `asleep` / `restless` / `awake`. We map
  both `asleep` and `restless` to `light` since no deep/REM signal is
  available — better to coarsely populate `light_min` than to leave the row
  blank or attribute time to a stage we don't actually know.

Fitbit timestamps are user-local *without* an offset. The caller must pass
the IANA timezone (e.g. `America/Los_Angeles`); we convert to UTC and store
the resolved offset in `tz_offset_minutes`, matching the Apple Health
parser's contract.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backend.agents.ingestion.base import (
    ParseResult,
    SleepSession,
    WearableParser,
)

logger = logging.getLogger(__name__)


STAGE_MAP_MODERN: dict[str, str] = {
    "deep": "deep",
    "light": "light",
    "rem": "rem",
    "wake": "awake",
}

# Legacy ("classic") devices report no stage breakdown. Map all sleep time to
# `light` (we don't know it was light — but light is the conservative default
# and avoids fabricating deep/REM totals we can't measure).
STAGE_MAP_LEGACY: dict[str, str] = {
    "asleep": "light",
    "restless": "light",
    "awake": "awake",
}


def _parse_local(value: str, tz: ZoneInfo) -> datetime:
    """Parse a Fitbit local timestamp and attach the user's timezone."""
    # Fitbit emits "2024-01-14T23:30:00.000" — fromisoformat handles ms.
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        # Defensive — older docs claim no offset; if one ever appears, honor it.
        return naive.astimezone(tz)
    return naive.replace(tzinfo=tz)


def _session_date(local_start: datetime) -> datetime:
    """A session that started before 6 PM local belongs to the previous day."""
    if local_start.hour < 18:
        anchor = local_start - timedelta(days=1)
    else:
        anchor = local_start
    return datetime(anchor.year, anchor.month, anchor.day, tzinfo=anchor.tzinfo)


def _stage_minutes_from_summary(summary: dict[str, Any], stage_map: dict[str, str]) -> dict[str, float]:
    """Pull per-stage minutes from `levels.summary`. Returns {deep, light, rem, awake}."""
    totals = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}
    for raw_stage, target in stage_map.items():
        block = summary.get(raw_stage)
        if not isinstance(block, dict):
            continue
        minutes = block.get("minutes")
        if minutes is None:
            continue
        totals[target] += float(minutes)
    return totals


def _stage_minutes_from_epochs(
    epochs: list[dict[str, Any]], stage_map: dict[str, str]
) -> dict[str, float]:
    """Sum per-stage seconds across `levels.data` epochs. Fallback when summary missing."""
    totals = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}
    for epoch in epochs:
        level = epoch.get("level")
        seconds = epoch.get("seconds")
        if level is None or seconds is None:
            continue
        target = stage_map.get(str(level).lower())
        if target is None:
            continue
        totals[target] += float(seconds) / 60.0
    return totals


def _entry_to_session(entry: dict[str, Any], tz: ZoneInfo) -> SleepSession | None:
    """Convert one Fitbit sleep entry to our unified `SleepSession`.

    Returns None if the entry is missing required start/end timestamps.
    """
    start_str = entry.get("startTime")
    end_str = entry.get("endTime")
    if not start_str or not end_str:
        return None

    local_start = _parse_local(start_str, tz)
    local_end = _parse_local(end_str, tz)

    sleep_start_utc = local_start.astimezone(timezone.utc)
    sleep_end_utc = local_end.astimezone(timezone.utc)

    # tz offset evaluated at the start of the session — DST transitions during
    # a single night are rare enough that the start offset is the right anchor.
    offset = local_start.utcoffset() or timedelta(0)
    tz_offset_min = int(offset.total_seconds() // 60)

    entry_type = (entry.get("type") or "stages").lower()
    stage_map = STAGE_MAP_LEGACY if entry_type == "classic" else STAGE_MAP_MODERN

    levels = entry.get("levels") or {}
    summary = levels.get("summary") if isinstance(levels, dict) else None
    epochs = levels.get("data") if isinstance(levels, dict) else None

    if isinstance(summary, dict) and summary:
        stage_min = _stage_minutes_from_summary(summary, stage_map)
    elif isinstance(epochs, list):
        stage_min = _stage_minutes_from_epochs(epochs, stage_map)
    else:
        stage_min = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

    minutes_asleep = entry.get("minutesAsleep")
    time_in_bed = entry.get("timeInBed")
    if time_in_bed is None:
        # Fall back to wall-clock window; matches Fitbit's own definition.
        time_in_bed = (sleep_end_utc - sleep_start_utc).total_seconds() / 60.0
    if minutes_asleep is None:
        minutes_asleep = max(
            0.0,
            float(time_in_bed) - stage_min["awake"],
        )

    time_in_bed = float(time_in_bed)
    minutes_asleep = float(minutes_asleep)
    efficiency = (minutes_asleep / time_in_bed) if time_in_bed > 0 else None

    return SleepSession(
        source="fitbit",
        date=_session_date(local_start),
        sleep_start=sleep_start_utc,
        sleep_end=sleep_end_utc,
        tz_offset_minutes=tz_offset_min,
        total_duration_min=minutes_asleep,
        time_in_bed_min=time_in_bed,
        deep_min=stage_min["deep"],
        light_min=stage_min["light"],
        rem_min=stage_min["rem"],
        awake_min=stage_min["awake"],
        efficiency=efficiency,
        avg_hr=None,  # Fitbit's sleep export doesn't bundle resting HR per night
    )


def _load_entries(file_path: Path) -> list[dict[str, Any]]:
    """Read the JSON file and return the entry list, accepting both shapes."""
    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("sleep"), list):
            return payload["sleep"]
    raise ValueError(
        f"Unexpected Fitbit JSON shape in {file_path}: "
        "expected a list of entries or a dict with a 'sleep' array"
    )


class FitbitParser(WearableParser):
    """Parser for Fitbit JSON sleep exports.

    Args:
        timezone_name: IANA timezone of the user (e.g. "America/Los_Angeles").
            Fitbit timestamps are user-local without offsets, so we need this
            to convert to UTC. Defaults to UTC if the caller knows the data is
            already UTC-anchored.
    """

    def __init__(self, timezone_name: str = "UTC") -> None:
        self._tz = ZoneInfo(timezone_name)
        self._timezone_name = timezone_name

    @property
    def source_name(self) -> str:
        return "fitbit"

    def parse(self, file_path: Path) -> ParseResult:
        try:
            entries = _load_entries(file_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            return ParseResult(errors=[f"Failed to load {file_path}: {e}"])

        sessions: list[SleepSession] = []
        errors: list[str] = []
        for idx, entry in enumerate(entries):
            try:
                session = _entry_to_session(entry, self._tz)
            except Exception as e:  # one bad entry shouldn't sink the whole file
                logger.exception("Failed to parse Fitbit entry #%d", idx)
                errors.append(f"entry #{idx}: {e}")
                continue
            if session is not None:
                sessions.append(session)

        # Dedup within a single file by (source, sleep_start, sleep_end) — same
        # key as the DB unique constraint. Fitbit re-syncs sometimes append a
        # duplicate entry; better to drop it here than rely on the DB to swallow
        # it during the upsert.
        seen: set[tuple[str, datetime, datetime]] = set()
        unique_sessions: list[SleepSession] = []
        for s in sessions:
            key = (s.source, s.sleep_start, s.sleep_end)
            if key in seen:
                continue
            seen.add(key)
            unique_sessions.append(s)

        return ParseResult(
            sessions=unique_sessions,
            workouts=[],
            errors=errors,
            records_processed=len(entries),
        )
