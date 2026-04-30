"""Exercise context adapter — aggregates per-day exercise from parsed workouts.

Operates on `WorkoutSession` data extracted by the Apple Health parser.
Computes per-day rollups: total minutes, primary type, intensity bucket.
"""

from collections import defaultdict
from datetime import date
from typing import Any

from backend.agents.context.base import ContextAdapter
from backend.agents.ingestion.base import WorkoutSession

# HR-based intensity thresholds (BPM). Generic adult-fitness defaults; future
# work could personalize via age / max-HR.
HR_LOW_MAX = 120
HR_MODERATE_MAX = 150

# Activity-type fallback when HR is missing
ACTIVITY_INTENSITY = {
    "HKWorkoutActivityTypeRunning": "high",
    "HKWorkoutActivityTypeCycling": "high",
    "HKWorkoutActivityTypeHiking": "moderate",
    "HKWorkoutActivityTypeSwimming": "high",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "high",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "moderate",
    "HKWorkoutActivityTypeWalking": "low",
    "HKWorkoutActivityTypeYoga": "low",
    "HKWorkoutActivityTypeMindAndBody": "low",
}

INTENSITY_RANK = {"low": 0, "moderate": 1, "high": 2}
RANK_INTENSITY = {v: k for k, v in INTENSITY_RANK.items()}


def classify_intensity(workout: WorkoutSession) -> str:
    """Bucket a single workout into 'low' | 'moderate' | 'high'."""
    if workout.avg_hr is not None:
        if workout.avg_hr < HR_LOW_MAX:
            return "low"
        if workout.avg_hr < HR_MODERATE_MAX:
            return "moderate"
        return "high"
    return ACTIVITY_INTENSITY.get(workout.activity_type, "moderate")


def workout_local_date(workout: WorkoutSession) -> date:
    """The date a workout is attributed to, in its local timezone."""
    return workout.start.date()


def aggregate_workouts_by_day(workouts: list[WorkoutSession]) -> dict[date, dict[str, Any]]:
    """Roll up a list of workouts into per-day exercise context dicts.

    Returns: {date: {exercise_min, exercise_type, exercise_intensity}}
    - exercise_min: sum of all workout durations that day
    - exercise_type: activity_type of the longest workout
    - exercise_intensity: highest intensity bucket touched that day
    """
    by_day: dict[date, list[WorkoutSession]] = defaultdict(list)
    for w in workouts:
        by_day[workout_local_date(w)].append(w)

    result: dict[date, dict[str, Any]] = {}
    for day, day_workouts in by_day.items():
        total_min = sum(w.duration_min for w in day_workouts)
        longest = max(day_workouts, key=lambda w: w.duration_min)
        max_rank = max(INTENSITY_RANK[classify_intensity(w)] for w in day_workouts)
        result[day] = {
            "exercise_min": round(total_min, 2),
            "exercise_type": longest.activity_type,
            "exercise_intensity": RANK_INTENSITY[max_rank],
        }
    return result


class ExerciseAdapter(ContextAdapter):
    """Returns per-date exercise context from a list of parsed workouts."""

    @property
    def adapter_name(self) -> str:
        return "exercise"

    async def fetch(self, target_date: date, **kwargs: Any) -> dict[str, Any]:
        """Fetch exercise context for a date.

        Args:
            target_date: The date to look up.
            **kwargs: Expected keys:
                - workouts: list[WorkoutSession] — already-parsed workout data

        Returns:
            Dict with exercise_min, exercise_type, exercise_intensity.
            Empty dict if no workouts that day.
        """
        workouts: list[WorkoutSession] = kwargs.get("workouts", [])
        rollup = aggregate_workouts_by_day(workouts)
        return rollup.get(target_date, {})
