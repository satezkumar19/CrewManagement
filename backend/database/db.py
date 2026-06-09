"""
Async SQLAlchemy engine + session factory for PostgreSQL.

The connection string comes from `settings.database_url`
(`postgresql+asyncpg://...`), which is loaded from backend/.env.
"""
import structlog
from sqlalchemy import text
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
    from database import decision_orm  # noqa: F401 — L4 decision_traces table
    from database import decision_audit_orm  # noqa: F401 — L4 HITL audit trail
    from database import placement_precedent_orm  # noqa: F401 — L4 precedent index

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # L4 #3 — pgvector extension for structural-embedding similarity. Best-effort in
    # its own transaction (mirrors the AGE bring-up): only attempted when the
    # pgvector backend is selected, and a missing extension degrades to the Python
    # fallback rather than blocking startup.
    if settings.vector_backend.lower() == "pgvector":
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            log.info("pgvector_enabled")
        except Exception as exc:  # noqa: BLE001
            log.warning("pgvector_extension_failed", error=str(exc))

    # Lightweight, idempotent migrations. create_all only CREATES missing tables —
    # it never ADDs columns to a table that already exists. The L4 #2 columns were
    # added to decision_traces after Phase 1 created it, so older databases need
    # them backfilled. Postgres supports ADD COLUMN IF NOT EXISTS; run best-effort
    # in its own transaction so a failure here can't roll back create_all.
    migrations = (
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS is_repeat_query BOOLEAN DEFAULT FALSE",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS consulted_precedents JSON",
        # L4 #3 — precedent feedback into L3 (re-rank measurement on the trace; the
        # chosen crew's profile on precedent rows is what the boost keys on).
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS precedent_feedback JSON",
        "ALTER TABLE placement_precedents ADD COLUMN IF NOT EXISTS chosen_crew_nationality VARCHAR",
        "ALTER TABLE placement_precedents ADD COLUMN IF NOT EXISTS chosen_crew_grade VARCHAR",
        # L4 #4 — rejection-retry loop: the per-candidate attempt journey + a
        # human reason for a still-pending decision.
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS attempts JSON",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS pending_reason VARCHAR",
        # L4 #3 — structural embedding on each crew row.
        "ALTER TABLE crew ADD COLUMN IF NOT EXISTS embedding JSON",
        # L4 HITL — human-in-the-loop review fields on the decision trace. Added after
        # Phase 1 created decision_traces, so older databases need them backfilled.
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS decision_source VARCHAR DEFAULT 'ai'",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS review_status VARCHAR",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS review_trigger VARCHAR",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS review_reason VARCHAR",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS review_comments VARCHAR",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS review_evidence JSON",
        "ALTER TABLE decision_traces ADD COLUMN IF NOT EXISTS ai_proposal JSON",
        # L4 HITL — attribution on the precedent row, so human-reviewed placements
        # can be weighted above AI auto-decisions in the matching feedback loop.
        "ALTER TABLE placement_precedents ADD COLUMN IF NOT EXISTS decision_source VARCHAR DEFAULT 'ai'",
        "ALTER TABLE placement_precedents ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR",
        "ALTER TABLE placement_precedents ADD COLUMN IF NOT EXISTS review_reason VARCHAR",
    )
    try:
        async with engine.begin() as conn:
            for stmt in migrations:
                await conn.execute(text(stmt))
    except Exception as exc:  # noqa: BLE001 - non-fatal; log and continue
        log.warning("db_migrate_failed", error=str(exc))

    # L4 #3 — ensure every crew row has a structural embedding (best-effort; safe to
    # run every startup since it only fills rows that lack one).
    try:
        from database.embedding_repository import backfill_embeddings
        await backfill_embeddings()
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding_backfill_failed", error=str(exc))

    log.info("db_initialized", url=settings.database_url.rsplit("@", 1)[-1])
