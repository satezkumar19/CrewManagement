"""
Structural Embeddings API (L4 #3).

Makes the crew structural embeddings queryable: find the crew most similar to a
given crew member (pgvector when enabled, Python-cosine fallback otherwise), plus a
convenience endpoint to (re)compute embeddings for the whole pool.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException

from config import settings
from database.embedding_repository import (
    backfill_embeddings,
    find_similar_crew,
    get_crew_embedding,
)

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.get("/similar/{crew_id}", response_model=dict)
async def similar_crew(crew_id: str, pool: Optional[str] = "signon", limit: int = 5):
    """Crew structurally most similar to `crew_id` (best-first).

    `pool` restricts the candidate pool ('signon' | 'signoff'); pass an empty value
    to search across both. Excludes the query crew from its own results.
    """
    query_vec = await get_crew_embedding(crew_id)
    if query_vec is None:
        raise HTTPException(status_code=404, detail=f"Crew {crew_id} not found")
    matches = await find_similar_crew(
        query_vec, pool=pool or None, limit=limit, exclude_id=crew_id
    )
    return {
        "crew_id": crew_id,
        "backend": settings.vector_backend,
        "count": len(matches),
        "matches": matches,
    }


@router.post("/backfill", response_model=dict)
async def backfill(force: bool = False):
    """(Re)compute structural embeddings for crew rows. `force=true` rebuilds all."""
    updated = await backfill_embeddings(force=force)
    return {"updated": updated, "backend": settings.vector_backend}
