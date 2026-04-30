"""OAuth2 credential management — encrypted storage, refresh, and retrieval.

The Fernet key in `settings.token_encryption_key` encrypts all secrets at rest.
Single-user system for now: tokens are keyed by `provider` (e.g. "google_calendar").
"""

import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import UserToken

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]
PROVIDER_GOOGLE_CALENDAR = "google_calendar"


class OAuthError(Exception):
    """Raised when OAuth credentials are missing, invalid, or unrefreshable."""


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise OAuthError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with "
            "Fernet.generate_key() and set it in your environment."
        )
    return Fernet(settings.token_encryption_key.encode())


def _encrypt(value: str | None) -> bytes | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode())


def _decrypt(value: bytes | None) -> str | None:
    if value is None:
        return None
    return _fernet().decrypt(value).decode()


def build_google_flow(state: str | None = None) -> Flow:
    """Construct an OAuth2 Flow for the Google Calendar consent screen."""
    if not settings.google_client_id or not settings.google_client_secret:
        raise OAuthError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured")

    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_CALENDAR_SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    if state is not None:
        flow.state = state
    return flow


async def save_credentials(
    provider: str, creds: Credentials, session: AsyncSession
) -> None:
    """Persist credentials encrypted. Upserts on the provider PK."""
    if creds.refresh_token is None:
        raise OAuthError(
            "Credentials missing refresh_token — Google must consent with "
            "access_type=offline and prompt=consent on the initial flow."
        )

    expires_at = creds.expiry
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    stmt = select(UserToken).where(UserToken.provider == provider)
    existing = (await session.execute(stmt)).scalar_one_or_none()

    payload = {
        "refresh_token_encrypted": _encrypt(creds.refresh_token),
        "access_token_encrypted": _encrypt(creds.token),
        "token_uri": creds.token_uri or "https://oauth2.googleapis.com/token",
        "client_id": creds.client_id or settings.google_client_id,
        "client_secret_encrypted": _encrypt(creds.client_secret or settings.google_client_secret),
        "scopes": " ".join(creds.scopes or []),
        "expires_at": expires_at,
    }

    if existing is None:
        token = UserToken(provider=provider, **payload)
        session.add(token)
    else:
        for k, v in payload.items():
            setattr(existing, k, v)

    await session.commit()


async def load_credentials(provider: str, session: AsyncSession) -> Credentials:
    """Load credentials and refresh if expired. Re-persists on refresh."""
    stmt = select(UserToken).where(UserToken.provider == provider)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise OAuthError(f"No credentials stored for provider '{provider}'")

    creds = Credentials(
        token=_decrypt(row.access_token_encrypted),
        refresh_token=_decrypt(row.refresh_token_encrypted),
        token_uri=row.token_uri,
        client_id=row.client_id,
        client_secret=_decrypt(row.client_secret_encrypted),
        scopes=row.scopes.split() if row.scopes else [],
    )
    if row.expires_at is not None:
        # google.oauth2.credentials uses naive UTC datetimes for expiry.
        creds.expiry = row.expires_at.astimezone(timezone.utc).replace(tzinfo=None)

    if not creds.valid:
        if not creds.refresh_token:
            raise OAuthError(f"Credentials for '{provider}' have no refresh token")
        try:
            creds.refresh(Request())
        except Exception as e:  # google.auth.exceptions.RefreshError, etc.
            raise OAuthError(f"Failed to refresh credentials for '{provider}': {e}") from e
        await save_credentials(provider, creds, session)

    return creds
