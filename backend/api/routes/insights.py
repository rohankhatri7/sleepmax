"""Insight generation endpoints (Agent 4)."""

import json
import logging
from datetime import date as date_type, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.insights import (
    GeminiInsightGenerator,
    InsightGeneratorError,
    PatternInput,
    RateLimitedError,
)
from backend.api.schemas import InsightGenerateRequest, InsightOut
from backend.config import settings
from backend.db.database import get_session
from backend.db.models import DiscoveredPattern, Insight

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _row_to_out(row: Insight) -> InsightOut:
    return InsightOut(
        id=row.id,
        generated_for_date=row.generated_for_date,
        weekly_digest=row.weekly_digest,
        insights=json.loads(row.insights_json),
        patterns_used=json.loads(row.patterns_used),
        model_name=row.model_name,
        created_at=row.created_at,
    )


@router.post("/generate", response_model=InsightOut)
async def generate_insights(
    req: InsightGenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> InsightOut:
    """Generate insights from the latest discovered patterns and persist them.

    Reads the top patterns (ranked by `correlation_strength * confidence`) from
    `discovered_patterns`, calls Gemini Flash to translate them into prose, and
    writes the result to `insights`.
    """
    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured")

    for_date = req.for_date or datetime.now(timezone.utc).date()

    stmt = (
        select(DiscoveredPattern)
        .order_by(desc(DiscoveredPattern.created_at))
        .limit(50)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars())

    # Rank by effect-size × confidence; cap the prompt at 10 patterns to keep
    # token usage low and the digest focused.
    ranked = sorted(
        rows,
        key=lambda r: abs(r.correlation_strength) * r.confidence,
        reverse=True,
    )[:10]

    pattern_inputs = [
        PatternInput(
            pattern_type=r.pattern_type,
            context_field=r.context_field,
            sleep_metric=r.sleep_metric,
            correlation_strength=r.correlation_strength,
            confidence=r.confidence,
            description=r.description,
            sample_size=r.sample_size,
        )
        for r in ranked
    ]

    generator = GeminiInsightGenerator(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
    )
    try:
        output = generator.generate(pattern_inputs)
    except RateLimitedError as exc:
        logger.warning("Gemini rate limit hit: %s", exc)
        raise HTTPException(status_code=429, detail=str(exc))
    except InsightGeneratorError as exc:
        logger.exception("Insight generation failed")
        raise HTTPException(status_code=503, detail=str(exc))

    row = Insight(
        generated_for_date=for_date,
        weekly_digest=output.weekly_digest,
        insights_json=json.dumps(output.insights),
        patterns_used=json.dumps([str(r.id) for r in ranked]),
        model_name=output.model,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    return _row_to_out(row)


@router.get("", response_model=InsightOut | None)
async def get_latest_insight(
    for_date: date_type | None = Query(None, description="Filter to a specific date"),
    session: AsyncSession = Depends(get_session),
) -> InsightOut | None:
    """Return the most recent persisted digest (optionally filtered by date)."""
    stmt = select(Insight).order_by(desc(Insight.created_at)).limit(1)
    if for_date is not None:
        stmt = (
            select(Insight)
            .where(Insight.generated_for_date == for_date)  # type: ignore[arg-type]
            .order_by(desc(Insight.created_at))
            .limit(1)
        )

    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _row_to_out(row)
