"""Shared streaming-XML helpers for Apple Health export parsing.

All callers should use these helpers instead of raw lxml.iterparse to ensure
the standard memory-cleanup pattern (clear element + drop already-processed
siblings) is applied consistently.
"""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from lxml import etree

# Apple Health date format: "2024-01-15 23:30:00 -0800"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"


def parse_health_date(date_str: str) -> datetime:
    """Parse an Apple Health attribute date string into a tz-aware datetime."""
    return datetime.strptime(date_str, DATE_FORMAT)


def tz_offset_minutes(dt: datetime) -> int:
    """Extract timezone offset in minutes from a tz-aware datetime."""
    offset = dt.utcoffset()
    if offset is None:
        return 0
    return int(offset.total_seconds() / 60)


def iter_elements(
    file_path: Path,
    tag: str,
    type_filter: set[str] | None = None,
) -> Iterator[etree._Element]:
    """Stream `<{tag}>` elements from an Apple Health XML, with memory cleanup.

    Args:
        file_path: Path to the export XML.
        tag: Element tag to match (e.g. "Record", "Workout").
        type_filter: If given, only yield elements whose `type` attribute is in this set.
            Elements outside the filter are still cleared from memory.

    Yields:
        The matching element. The caller MUST NOT retain references after the
        next iteration — `elem.clear()` runs immediately after yielding.
    """
    context = etree.iterparse(str(file_path), events=("end",), tag=tag)
    for _, elem in context:
        if type_filter is None or elem.get("type") in type_filter:
            yield elem

        elem.clear()
        # Drop earlier siblings to release memory
        while elem.getprevious() is not None:
            parent = elem.getparent()
            if parent is None:
                break
            del parent[0]
