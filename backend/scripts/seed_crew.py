"""
Seed the Postgres `crew` table from the bundled dataset
(20 sign-on candidates + 20 sign-off crew).

Idempotent: drops and recreates the `crew` table, then inserts a fresh snapshot.
Run once after the database exists:

    python -m scripts.seed_crew
"""
import asyncio

from database.crew_orm import Crew
from database.db import AsyncSessionLocal, Base, engine
from mock_data.crew_data import get_sign_off_crew, get_sign_on_crew


def _to_row(data: dict, pool: str) -> Crew:
    return Crew(
        crew_id=data["crew_id"],
        pool=pool,
        name=data.get("name"),
        rank=data.get("rank"),
        grade=data.get("grade"),
        nationality=data.get("nationality"),
        vessel=data.get("vessel"),
        port=data.get("port"),
        joining_date=data.get("joining_date"),
        medical_expiry=data.get("medical_expiry"),
        passport_expiry=data.get("passport_expiry"),
        stcw_status=data.get("stcw_status", "Valid"),
        visa_status=data.get("visa_status", "Valid"),
        availability=data.get("availability"),
        experience_years=data.get("experience_years"),
        certifications=data.get("certifications"),
        match_score=data.get("match_score"),
        match_reason=data.get("match_reason"),
        status=data.get("status", "Available"),
    )


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    sign_on = get_sign_on_crew()
    sign_off = get_sign_off_crew()

    async with AsyncSessionLocal() as session:
        session.add_all([_to_row(c, "signon") for c in sign_on])
        session.add_all([_to_row(c, "signoff") for c in sign_off])
        await session.commit()

    # L4 #3 — compute the structural embedding for every freshly-seeded crew row.
    from database.embedding_repository import backfill_embeddings
    embedded = await backfill_embeddings(force=True)

    print(
        f"Seeded {len(sign_on)} sign-on + {len(sign_off)} sign-off crew into Postgres "
        f"({embedded} embeddings)."
    )


if __name__ == "__main__":
    asyncio.run(seed())
