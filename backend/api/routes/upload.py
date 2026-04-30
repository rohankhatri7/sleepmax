"""Upload endpoints for wearable data exports.

Apple Health uploads enqueue a Celery task and return 202 Accepted with a
job ID. Clients poll `/api/upload/status/{job_id}` for completion. This
keeps the request short even for multi-GB exports.
"""

import logging
import uuid
from pathlib import Path

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, UploadFile, status

from backend.api.schemas import UploadEnqueueResponse, UploadStatusResponse
from backend.config import settings
from backend.tasks import parse_apple_health
from backend.worker import app as celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])

UPLOAD_DIR = Path("/tmp/sleepmax/uploads")


@router.post(
    "/apple-health",
    response_model=UploadEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_apple_health(file: UploadFile) -> UploadEnqueueResponse:
    """Stream the upload to a shared volume and enqueue a parse task.

    Returns 202 with a job ID; clients should poll the status endpoint.
    """
    if not file.filename or not file.filename.endswith(".xml"):
        raise HTTPException(status_code=400, detail="File must be an XML file")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    target = UPLOAD_DIR / f"{upload_id}.xml"

    # Stream to disk to avoid loading the full file in memory
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1 << 20)  # 1 MiB
            if not chunk:
                break
            out.write(chunk)

    task = parse_apple_health.delay(str(target))
    logger.info("Enqueued parse_apple_health for %s as job %s", target, task.id)

    return UploadEnqueueResponse(
        status="accepted",
        job_id=task.id,
        status_url=f"/api/upload/status/{task.id}",
    )


@router.get("/status/{job_id}", response_model=UploadStatusResponse)
async def upload_status(job_id: str) -> UploadStatusResponse:
    """Return the current state of a parse job."""
    result = AsyncResult(job_id, app=celery_app)
    state = result.state

    if state == "FAILURE":
        return UploadStatusResponse(
            state="failed",
            error=str(result.info) if result.info else "unknown error",
        )

    info = result.info if isinstance(result.info, dict) else None
    return UploadStatusResponse(
        state=state.lower(),
        meta=info,
        result=result.result if state == "SUCCESS" else None,
    )
