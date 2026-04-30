"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://sleepmax:sleepmax@localhost:5432/sleepmax"
    database_url_sync: str = "postgresql+psycopg2://sleepmax:sleepmax@localhost:5432/sleepmax"
    redis_url: str = "redis://localhost:6379/0"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # Fernet key for encrypting OAuth tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

    # Google Gemini API key for Agent 4 (insight generation).
    # Free tier: 15 req/min, 1M tokens/min, 1500 req/day — plenty for a daily digest.
    # Get one at https://aistudio.google.com/apikey
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    default_latitude: float = 37.7749
    default_longitude: float = -122.4194

    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
