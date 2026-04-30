"""Open-Meteo weather context adapter — fully functional, no auth needed.

Fetches daily weather summary (temperature, precipitation) and hourly data
(humidity, pressure), then computes daily averages for the context vector.
"""

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from backend.agents.context.base import ContextAdapter

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Be polite — limit to 1 request per second
_rate_limit_lock = asyncio.Lock()
_last_request_time: float = 0.0


async def _rate_limited_get(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """Make a GET request with 1 req/sec rate limiting."""
    global _last_request_time
    async with _rate_limit_lock:
        now = asyncio.get_event_loop().time()
        wait = max(0, 1.0 - (now - _last_request_time))
        if wait > 0:
            await asyncio.sleep(wait)
        response = await client.get(url, params=params)
        _last_request_time = asyncio.get_event_loop().time()
    return response


def _pick_endpoint(target_date: date) -> str:
    """Use forecast API for recent/future dates, archive for historical."""
    days_ago = (date.today() - target_date).days
    if days_ago > 7:
        return ARCHIVE_URL
    return FORECAST_URL


class WeatherAdapter(ContextAdapter):
    """Fetches weather data from Open-Meteo and returns partial DailyContext fields."""

    @property
    def adapter_name(self) -> str:
        return "weather"

    async def fetch(self, target_date: date, **kwargs: Any) -> dict[str, Any]:
        """Fetch weather context for a given date and location.

        Args:
            target_date: The date to fetch weather for.
            **kwargs: Expected keys:
                - latitude: float (required)
                - longitude: float (required)

        Returns:
            Dict with keys: temp_high_c, temp_low_c, humidity_pct,
            pressure_hpa, precipitation_mm.
        """
        latitude = kwargs.get("latitude")
        longitude = kwargs.get("longitude")
        if latitude is None or longitude is None:
            raise ValueError("latitude and longitude are required for weather lookup")

        date_str = target_date.isoformat()
        endpoint = _pick_endpoint(target_date)

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": date_str,
            "end_date": date_str,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "hourly": "relative_humidity_2m,surface_pressure",
            "timezone": "auto",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await _rate_limited_get(client, endpoint, params)
            response.raise_for_status()
            data = response.json()

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> dict[str, Any]:
        """Extract and compute daily weather metrics from the API response."""
        result: dict[str, Any] = {}

        daily = data.get("daily", {})
        if daily:
            temps_max = daily.get("temperature_2m_max", [])
            temps_min = daily.get("temperature_2m_min", [])
            precip = daily.get("precipitation_sum", [])

            result["temp_high_c"] = temps_max[0] if temps_max else None
            result["temp_low_c"] = temps_min[0] if temps_min else None
            result["precipitation_mm"] = precip[0] if precip else None

        hourly = data.get("hourly", {})
        if hourly:
            humidity_values = [v for v in hourly.get("relative_humidity_2m", []) if v is not None]
            pressure_values = [v for v in hourly.get("surface_pressure", []) if v is not None]

            result["humidity_pct"] = (
                round(sum(humidity_values) / len(humidity_values), 1)
                if humidity_values else None
            )
            result["pressure_hpa"] = (
                round(sum(pressure_values) / len(pressure_values), 1)
                if pressure_values else None
            )

        return result
