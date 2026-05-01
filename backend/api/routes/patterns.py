"""Pattern discovery (Agent 3) endpoints."""

import json
import logging
from typing import Any

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_session
from backend.db.models import DiscoveredPattern
from backend.tasks import run_discovery
from backend.worker import app as celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


def _row_to_dict(row: DiscoveredPattern) -> dict[str, Any]:
    confounded_with: list[str] = []
    if row.confounded_with:
        try:
            parsed = json.loads(row.confounded_with)
            if isinstance(parsed, list):
                confounded_with = [str(x) for x in parsed]
        except json.JSONDecodeError:
            logger.warning("malformed confounded_with on pattern %s", row.id)
    return {
        "id": str(row.id),
        "pattern_type": row.pattern_type,
        "context_field": row.context_field,
        "sleep_metric": row.sleep_metric,
        "correlation_strength": row.correlation_strength,
        "confidence": row.confidence,
        "p_value": row.p_value,
        "lag_days": row.lag_days,
        "threshold": row.threshold,
        "confidence_label": row.confidence_label,
        "description": row.description,
        "sample_size": row.sample_size,
        "confound_flag": row.confound_flag,
        "confounded_with": confounded_with,
    }


@router.post("/discover", status_code=status.HTTP_202_ACCEPTED)
def discover() -> dict:
    """Enqueue a discovery run. Returns the Celery task id for status polling."""
    task = run_discovery.delay()
    return {
        "status": "accepted",
        "task_id": task.id,
        "status_url": f"/api/patterns/status/{task.id}",
    }


@router.get("/status/{task_id}")
def discover_status(task_id: str) -> dict:
    """Report the state of a discovery task."""
    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    if state == "FAILURE":
        return {"state": "failed", "error": str(result.info) if result.info else "unknown"}
    info = result.info if isinstance(result.info, dict) else None
    return {
        "state": state.lower(),
        "meta": info,
        "result": result.result if state == "SUCCESS" else None,
    }


@router.get("")
async def list_patterns(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return persisted patterns ranked by `|effect| × confidence` (matches discovery order)."""
    rows = (await session.execute(select(DiscoveredPattern))).scalars().all()
    ranked = sorted(
        rows,
        key=lambda r: abs(r.correlation_strength) * r.confidence,
        reverse=True,
    )
    return {
        "patterns": [_row_to_dict(r) for r in ranked],
        "count": len(ranked),
    }
