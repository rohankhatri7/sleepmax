"""Fitbit JSON export parser — stub with interface defined."""

from pathlib import Path

from backend.agents.ingestion.base import ParseResult, WearableParser


class FitbitParser(WearableParser):
    """Parser for Fitbit JSON sleep exports.

    Fitbit exports sleep data as JSON with detailed sleep stage information.
    Expected format: array of sleep objects with 'levels.data' containing
    stage transitions (wake, light, deep, rem).
    """

    @property
    def source_name(self) -> str:
        return "fitbit"

    def parse(self, file_path: Path) -> ParseResult:
        raise NotImplementedError(
            "Fitbit parser not yet implemented. "
            "Expected input: Fitbit JSON export from https://www.fitbit.com/settings/data/export"
        )
