"""Google Calendar context adapter — daily meeting metrics.

Pulls events from the user's primary calendar via the Calendar API v3 and
aggregates them into the fields the discovery agent cares about.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.context.base import ContextAdapter
from backend.services.oauth import (
    PROVIDER_GOOGLE_CALENDAR,
    OAuthError,
    load_credentials,
)

logger = logging.getLogger(__name__)

BACK_TO_BACK_GAP_MIN = 15


class CalendarUnavailable(Exception):
    """Raised when calendar data cannot be fetched (no auth, API error, etc.)."""


def _parse_event_dt(value: dict | None) -> datetime | None:
    """Parse the start/end value of a Calendar event. Returns None for all-day events."""
    if not value or "dateTime" not in value:
        return None
    return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))


def aggregate_events(events: list[dict]) -> dict[str, Any]:
    """Aggregate raw calendar events into the DailyContext fields.

    Skips all-day events. Returns: meeting_count, meeting_hours,
    first_meeting_time, last_meeting_time, back_to_back_count.
    """
    timed = []
    for ev in events:
        start = _parse_event_dt(ev.get("start"))
        end = _parse_event_dt(ev.get("end"))
        if start is None or end is None:
            continue
        if ev.get("status") == "cancelled":
            continue
        timed.append((start, end))

    if not timed:
        return {
            "meeting_count": 0,
            "meeting_hours": 0.0,
            "first_meeting_time": None,
            "last_meeting_time": None,
            "back_to_back_count": 0,
        }

    timed.sort(key=lambda x: x[0])

    total_hours = sum((e - s).total_seconds() / 3600 for s, e in timed)
    first_start = timed[0][0]
    last_end = timed[-1][1]

    back_to_back = 0
    for i in range(1, len(timed)):
        gap_min = (timed[i][0] - timed[i - 1][1]).total_seconds() / 60
        if 0 <= gap_min < BACK_TO_BACK_GAP_MIN:
            back_to_back += 1

    return {
        "meeting_count": len(timed),
        "meeting_hours": round(total_hours, 2),
        "first_meeting_time": first_start.strftime("%H:%M"),
        "last_meeting_time": last_end.strftime("%H:%M"),
        "back_to_back_count": back_to_back,
    }


class CalendarAdapter(ContextAdapter):
    """Fetches daily meeting metrics from Google Calendar."""

    @property
    def adapter_name(self) -> str:
        return "calendar"

    async def fetch(self, target_date: date, **kwargs: Any) -> dict[str, Any]:
        """Fetch meeting metrics for `target_date`.

        Args:
            target_date: the calendar date to query (in user's local TZ).
            session: AsyncSession kwarg, required to load credentials.
            tz_offset_minutes: optional int, day boundaries shift by this offset.
                Defaults to 0 (UTC day).

        Raises:
            CalendarUnavailable: when credentials are missing or the API errors.
        """
        session: AsyncSession | None = kwargs.get("session")
        if session is None:
            raise ValueError("`session` (AsyncSession) is required")
        tz_offset_minutes = int(kwargs.get("tz_offset_minutes", 0))

        try:
            creds = await load_credentials(PROVIDER_GOOGLE_CALENDAR, session)
        except OAuthError as e:
            raise CalendarUnavailable(str(e)) from e

        tz = timezone(timedelta(minutes=tz_offset_minutes))
        day_start = datetime.combine(target_date, time.min, tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        try:
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            events = []
            page_token: str | None = None
            while True:
                resp = (
                    service.events()
                    .list(
                        calendarId="primary",
                        timeMin=day_start.isoformat(),
                        timeMax=day_end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
                events.extend(resp.get("items", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as e:
            raise CalendarUnavailable(f"Calendar API error: {e}") from e

        return aggregate_events(events)
