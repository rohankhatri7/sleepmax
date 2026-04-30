"""OAuth2 authentication routes — Google Calendar consent flow."""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_session
from backend.services.oauth import (
    PROVIDER_GOOGLE_CALENDAR,
    OAuthError,
    build_google_flow,
    save_credentials,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/start")
async def google_start() -> RedirectResponse:
    """Redirect the user to Google's OAuth2 consent screen.

    `access_type=offline` ensures we receive a refresh token.
    `prompt=consent` forces re-consent so the refresh token is regranted on every flow.
    """
    try:
        flow = build_google_flow(state=secrets.token_urlsafe(16))
    except OAuthError as e:
        raise HTTPException(status_code=500, detail=str(e))

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(url=auth_url, status_code=307)


@router.get("/google/callback")
async def google_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Exchange the auth code for credentials and store them encrypted."""
    try:
        flow = build_google_flow(state=state)
        flow.fetch_token(code=code)
        await save_credentials(PROVIDER_GOOGLE_CALENDAR, flow.credentials, session)
    except OAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Google OAuth callback failed")
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")

    return {"status": "ok", "provider": PROVIDER_GOOGLE_CALENDAR}
