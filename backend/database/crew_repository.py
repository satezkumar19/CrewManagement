"""
Crew data-access layer — async, Postgres-backed.

Drop-in replacement for `mock_data.crew_data`: same function names and return
shapes (list[dict] / dict | None), so call sites only need to `await` them.
"""
from typing import Optional

from sqlalchemy import select

from database.crew_orm import Crew
from database.db import AsyncSessionLocal


async def get_sign_on_crew() -> list[dict]:
    """All crew available for sign-on."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Crew).where(Crew.pool == "signon").order_by(Crew.crew_id)
        )
        return [row.to_dict() for row in result.scalars().all()]


async def get_sign_off_crew() -> list[dict]:
    """All crew currently onboard (available for sign-off)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Crew).where(Crew.pool == "signoff").order_by(Crew.crew_id)
        )
        return [row.to_dict() for row in result.scalars().all()]


async def get_crew_by_id(crew_id: str, pool: str = "both") -> Optional[dict]:
    """Look up a single crew member, optionally restricted to a pool."""
    async with AsyncSessionLocal() as session:
        query = select(Crew).where(Crew.crew_id == crew_id)
        if pool == "signon":
            query = query.where(Crew.pool == "signon")
        elif pool == "signoff":
            query = query.where(Crew.pool == "signoff")
        row = (await session.execute(query)).scalar_one_or_none()
        return row.to_dict() if row else None


async def update_crew(
    crew_id: str,
    *,
    pool: Optional[str] = None,
    status: Optional[str] = None,
    match_score: Optional[float] = None,
    match_reason: Optional[str] = None,
) -> Optional[dict]:
    """Patch a crew row in place. Only the provided fields are written.

    Returns the updated row as a dict, or None if the crew_id doesn't exist.
    """
    async with AsyncSessionLocal() as session:
        crew = await session.get(Crew, crew_id)
        if crew is None:
            return None
        if pool is not None:
            crew.pool = pool
        if status is not None:
            crew.status = status
        if match_score is not None:
            crew.match_score = match_score
        if match_reason is not None:
            crew.match_reason = match_reason
        await session.commit()
        return crew.to_dict()
