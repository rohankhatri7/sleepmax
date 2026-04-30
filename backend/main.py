"""FastAPI application entry point for SleepMax."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.routes import auth, context, insights, sleep, upload
from backend.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Schema is managed by Alembic — run `alembic upgrade head` before starting."""
    yield


app = FastAPI(
    title="SleepMax",
    description="Multi-agent sleep analytics API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(upload.router)
app.include_router(sleep.router)
app.include_router(context.router)
app.include_router(auth.router)
app.include_router(insights.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
