"""Database engine, session factory, and initialization."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings

engine = create_async_engine(settings.database_url, echo=False)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """Yield an async database session for FastAPI dependency injection."""
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create all tables directly from metadata. Test-only — production uses Alembic."""
    from backend.db.models import Base  # noqa: F811

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
