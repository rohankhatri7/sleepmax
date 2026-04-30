"""Oura Ring CSV export parser — stub with interface defined."""

from pathlib import Path

from backend.agents.ingestion.base import ParseResult, WearableParser


class OuraParser(WearableParser):
    """Parser for Oura Ring CSV sleep exports.

    Oura provides CSV exports with columns for bedtime_start, bedtime_end,
    duration, deep, light, rem, awake, efficiency, hr_average, etc.
    """

    @property
    def source_name(self) -> str:
        return "oura"

    def parse(self, file_path: Path) -> ParseResult:
        raise NotImplementedError(
            "Oura parser not yet implemented. "
            "Expected input: Oura CSV export from cloud.ouraring.com"
        )
