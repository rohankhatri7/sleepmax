"""Abstract base class for wearable data parsers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SleepSession:
    """Intermediate representation of a parsed sleep session before DB insertion."""

    source: str
    date: datetime  # the calendar date of the night (evening start)
    sleep_start: datetime
    sleep_end: datetime
    tz_offset_minutes: int
    total_duration_min: float
    time_in_bed_min: float
    deep_min: float = 0.0
    light_min: float = 0.0
    rem_min: float = 0.0
    awake_min: float = 0.0
    efficiency: float | None = None
    avg_hr: float | None = None


@dataclass
class WorkoutSession:
    """Intermediate representation of a parsed workout/exercise event."""

    activity_type: str  # raw HKWorkoutActivityType string
    start: datetime
    end: datetime
    duration_min: float
    avg_hr: float | None = None  # mean HR during the workout window, if available


@dataclass
class ParseResult:
    """Result of parsing a wearable export file."""

    sessions: list[SleepSession] = field(default_factory=list)
    workouts: list[WorkoutSession] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    records_processed: int = 0


class WearableParser(ABC):
    """Interface that all wearable parsers must implement."""

    @abstractmethod
    def parse(self, file_path: Path) -> ParseResult:
        """Parse a wearable export file and return sleep sessions.

        Args:
            file_path: Path to the export file on disk.

        Returns:
            ParseResult with extracted sleep sessions and any parsing errors.
        """
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Identifier for this data source (e.g. 'apple_health', 'fitbit')."""
        ...
