"""
Async SQLAlchemy engine + session factory for PostgreSQL.

The connection string comes from `settings.database_url`
(`postgresql+asyncpg://...`), which is loaded from backend/.env.
"""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from config import settings

log = structlog.get_logger()

# `create_async_engine` requires the asyncpg driver URL (postgresql+asyncpg://).
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def init_db() -> None:
    """Create tables that don't exist yet. Safe to call on every startup."""
    # Import models so they register on Base.metadata before create_all.
    from database import crew_orm  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db_initialized", url=settings.database_url.rsplit("@", 1)[-1])
