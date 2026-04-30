"""Apple Health XML export parser using streaming iterparse.

Extracts sleep analysis records, heart rate data, and workouts from Apple
Health exports. Sleep records are grouped into per-night sessions with
stage-level totals.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.agents.ingestion._xml_utils import (
    iter_elements,
    parse_health_date,
    tz_offset_minutes,
)
from backend.agents.ingestion.base import (
    ParseResult,
    SleepSession,
    WearableParser,
    WorkoutSession,
)

logger = logging.getLogger(__name__)

# Apple Health sleep stage value mappings
SLEEP_STAGE_MAP = {
    "HKCategoryValueSleepAnalysisAsleepDeep": "deep",
    "HKCategoryValueSleepAnalysisAsleepCore": "light",
    "HKCategoryValueSleepAnalysisAsleepREM": "rem",
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "light",
    "HKCategoryValueSleepAnalysisAwake": "awake",
    "HKCategoryValueSleepAnalysisInBed": "in_bed",
    # Legacy (pre-iOS 16)
    "HKCategoryValueSleepAnalysisAsleep": "asleep_legacy",
}

SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
HR_TYPE = "HKQuantityTypeIdentifierHeartRate"

SESSION_GAP_MINUTES = 30


def _session_date(sleep_start: datetime) -> datetime:
    """Determine the calendar date for a sleep session.

    If sleep started before 6 PM local, attribute it to the previous day.
    Otherwise the session date is the start date.
    """
    local_dt = sleep_start
    if local_dt.hour < 18:
        return datetime(
            local_dt.year, local_dt.month, local_dt.day,
            tzinfo=local_dt.tzinfo,
        ) - timedelta(days=1)
    return datetime(local_dt.year, local_dt.month, local_dt.day, tzinfo=local_dt.tzinfo)


class AppleHealthParser(WearableParser):
    """Streaming parser for Apple Health XML exports."""

    @property
    def source_name(self) -> str:
        return "apple_health"

    def parse(self, file_path: Path) -> ParseResult:
        """Parse an Apple Health export. Three streaming passes:

        1. Sleep records → grouped into sessions
        2. Workouts → extracted as workout sessions
        3. Heart rate → averaged into both sleep sessions and workouts
        """
        sleep_records = self._extract_sleep_records(file_path)
        sessions = self._group_into_sessions(sleep_records)
        workouts = self._extract_workouts(file_path)
        self._attach_heart_rate(file_path, sessions, workouts)

        return ParseResult(
            sessions=sessions,
            workouts=workouts,
            records_processed=len(sleep_records),
        )

    def _extract_sleep_records(self, file_path: Path) -> list[dict]:
        """Stream through XML extracting sleep analysis records."""
        records = []
        for elem in iter_elements(file_path, "Record", type_filter={SLEEP_TYPE}):
            value = elem.get("value", "")
            stage = SLEEP_STAGE_MAP.get(value)
            if stage is None:
                continue
            try:
                start = parse_health_date(elem.get("startDate", ""))
                end = parse_health_date(elem.get("endDate", ""))
            except (ValueError, TypeError) as e:
                logger.warning("Skipping record with invalid date: %s", e)
                continue
            records.append({
                "stage": stage,
                "start": start,
                "end": end,
                "duration_min": (end - start).total_seconds() / 60,
            })

        records.sort(key=lambda r: r["start"])
        return records

    def _group_into_sessions(self, records: list[dict]) -> list[SleepSession]:
        """Group sleep records into sessions based on time gaps."""
        if not records:
            return []

        groups: list[list[dict]] = []
        current_group: list[dict] = [records[0]]

        for record in records[1:]:
            prev_end = current_group[-1]["end"]
            gap = (record["start"] - prev_end).total_seconds() / 60
            if gap > SESSION_GAP_MINUTES:
                groups.append(current_group)
                current_group = [record]
            else:
                current_group.append(record)
        groups.append(current_group)

        return [self._build_session(g) for g in groups if g]

    def _build_session(self, records: list[dict]) -> SleepSession:
        """Build a SleepSession from a group of records."""
        sleep_start = records[0]["start"]
        sleep_end = records[-1]["end"]

        stage_totals: dict[str, float] = defaultdict(float)
        for r in records:
            stage_totals[r["stage"]] += r["duration_min"]

        time_in_bed = (sleep_end - sleep_start).total_seconds() / 60

        deep = stage_totals.get("deep", 0.0)
        light = stage_totals.get("light", 0.0) + stage_totals.get("asleep_legacy", 0.0)
        rem = stage_totals.get("rem", 0.0)
        awake = stage_totals.get("awake", 0.0)
        in_bed_only = stage_totals.get("in_bed", 0.0)

        total_sleep = deep + light + rem
        total_duration = total_sleep + awake
        actual_in_bed = (
            time_in_bed if in_bed_only == 0 else max(time_in_bed, in_bed_only + total_duration)
        )

        efficiency = (total_sleep / actual_in_bed * 100) if actual_in_bed > 0 else None

        return SleepSession(
            source="apple_health",
            date=_session_date(sleep_start),
            sleep_start=sleep_start,
            sleep_end=sleep_end,
            tz_offset_minutes=tz_offset_minutes(sleep_start),
            total_duration_min=total_duration,
            time_in_bed_min=actual_in_bed,
            deep_min=deep,
            light_min=light,
            rem_min=rem,
            awake_min=awake,
            efficiency=efficiency,
        )

    def _extract_workouts(self, file_path: Path) -> list[WorkoutSession]:
        """Stream `<Workout>` elements into WorkoutSession dataclasses."""
        workouts: list[WorkoutSession] = []
        for elem in iter_elements(file_path, "Workout"):
            try:
                start = parse_health_date(elem.get("startDate", ""))
                end = parse_health_date(elem.get("endDate", ""))
            except (ValueError, TypeError) as e:
                logger.warning("Skipping workout with invalid date: %s", e)
                continue

            activity = elem.get("workoutActivityType", "Unknown")
            duration_min = (end - start).total_seconds() / 60

            workouts.append(WorkoutSession(
                activity_type=activity,
                start=start,
                end=end,
                duration_min=duration_min,
            ))
        return workouts

    def _attach_heart_rate(
        self,
        file_path: Path,
        sessions: list[SleepSession],
        workouts: list[WorkoutSession],
    ) -> None:
        """Single HR pass that attaches mean HR to overlapping sleep sessions and workouts."""
        if not sessions and not workouts:
            return

        def to_utc(d: datetime) -> datetime:
            return d.astimezone(timezone.utc)

        sleep_windows = [
            (to_utc(s.sleep_start), to_utc(s.sleep_end), s) for s in sessions
        ]
        workout_windows = [
            (to_utc(w.start), to_utc(w.end), w) for w in workouts
        ]
        sleep_buckets: dict[int, list[float]] = {i: [] for i in range(len(sessions))}
        workout_buckets: dict[int, list[float]] = {i: [] for i in range(len(workouts))}

        for elem in iter_elements(file_path, "Record", type_filter={HR_TYPE}):
            try:
                ts = parse_health_date(elem.get("startDate", "")).astimezone(timezone.utc)
                value = float(elem.get("value", "0"))
            except (ValueError, TypeError):
                continue

            for i, (start, end, _) in enumerate(sleep_windows):
                if start <= ts <= end:
                    sleep_buckets[i].append(value)
                    break
            for i, (start, end, _) in enumerate(workout_windows):
                if start <= ts <= end:
                    workout_buckets[i].append(value)
                    break

        for i, hrs in sleep_buckets.items():
            if hrs:
                sessions[i].avg_hr = sum(hrs) / len(hrs)
        for i, hrs in workout_buckets.items():
            if hrs:
                workouts[i].avg_hr = sum(hrs) / len(hrs)
