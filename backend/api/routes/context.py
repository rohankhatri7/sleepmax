"""Context vector query and fetch endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.context.weather import WeatherAdapter
from backend.api.schemas import DailyContextOut, WeatherRequest
from backend.config import settings
from backend.db.database import get_session
from backend.db.models import DailyContext

router = APIRouter(prefix="/api/context", tags=["context"])


@router.get("", response_model=DailyContextOut | None)
async def get_context(
    date: date = Query(..., description="Date to look up context for"),
    session: AsyncSession = Depends(get_session),
) -> DailyContextOut | None:
    """Return the context vector for a specific date, or null if not found."""
    stmt = select(DailyContext).where(DailyContext.date == date)  # type: ignore[arg-type]
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return None
    return DailyContextOut.model_validate(row)


@router.post("/weather", response_model=dict)
async def fetch_weather(
    req: WeatherRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fetch weather data for a date and location, store it in the context table.

    Uses the configured default lat/lon if not provided.
    """
    adapter = WeatherAdapter()
    weather_data = await adapter.fetch(
        req.date,
        latitude=req.latitude,
        longitude=req.longitude,
    )

    # Upsert into daily_context
    stmt = select(DailyContext).where(DailyContext.date == req.date)  # type: ignore[arg-type]
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if ctx is None:
        ctx = DailyContext(date=req.date)  # type: ignore[arg-type]
        session.add(ctx)

    for key, value in weather_data.items():
        if hasattr(ctx, key):
            setattr(ctx, key, value)

    await session.commit()
    await session.refresh(ctx)

    return {"status": "ok", "date": req.date.isoformat(), "weather": weather_data}
