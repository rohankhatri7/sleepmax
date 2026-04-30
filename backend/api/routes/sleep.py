"""Sleep record query endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas import SleepRecordList, SleepRecordOut
from backend.db.database import get_session
from backend.db.models import SleepRecord

router = APIRouter(prefix="/api/sleep", tags=["sleep"])


@router.get("", response_model=SleepRecordList)
async def get_sleep_records(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    session: AsyncSession = Depends(get_session),
) -> SleepRecordList:
    """Return sleep records within the given date range."""
    stmt = (
        select(SleepRecord)
        .where(SleepRecord.date >= start_date)  # type: ignore[arg-type]
        .where(SleepRecord.date <= end_date)  # type: ignore[arg-type]
        .order_by(SleepRecord.date)
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    return SleepRecordList(
        records=[SleepRecordOut.model_validate(r) for r in records],
        count=len(records),
    )
