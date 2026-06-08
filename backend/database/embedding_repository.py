"""
Structural-embedding data-access layer (L4 #3) — async, Postgres-backed.

Stores each crew row's structural embedding (a JSON float list on `crew.embedding`)
and answers similarity queries two ways, mirroring the GRAPH_BACKEND convention:

* VECTOR_BACKEND=pgvector → similarity runs IN the database via pgvector's `<=>`
  cosine-distance operator (the JSON array casts straight to a `vector` literal).
* VECTOR_BACKEND=fallback → embeddings are loaded and cosine is computed in Python.

Both return the same shape, so callers don't care which path ran. Best-effort:
failures are logged and degrade to the Python path rather than raising.
"""
from typing import List, Optional, Sequence

import structlog
from sqlalchemy import select, text

from config import settings
from database.crew_orm import Crew
from database.db import AsyncSessionLocal
from services.embedding_service import EMBED_DIM, cosine, embed_crew

log = structlog.get_logger()


def _pgvector_enabled() -> bool:
    return settings.vector_backend.lower() == "pgvector"


def _vec_literal(vec: Sequence[float]) -> str:
    """pgvector text literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def backfill_embeddings(force: bool = False) -> int:
    """Compute + store `embedding` for crew rows that lack one (or all, if force).

    Returns the number of rows updated. Best-effort; invalidates the crew cache so
    the next list reflects the new embeddings. Never raises.
    """
    updated = 0
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(Crew))).scalars().all()
            for row in rows:
                if not force and row.embedding:
                    continue
                row.embedding = embed_crew(row.to_dict())
                updated += 1
            if updated:
                await session.commit()
        if updated:
            await _invalidate_crew_cache()
        log.info("embeddings.backfilled", count=updated, backend=settings.vector_backend)
    except Exception:
        log.warning("embeddings.backfill_failed", exc_info=True)
    return updated


async def get_crew_embedding(crew_id: str) -> Optional[List[float]]:
    """The stored embedding for a crew member, computing+persisting it on demand."""
    async with AsyncSessionLocal() as session:
        row = await session.get(Crew, crew_id)
        if row is None:
            return None
        if row.embedding:
            return row.embedding
        emb = embed_crew(row.to_dict())
        row.embedding = emb
        await session.commit()
        return emb


async def find_similar_crew(
    query_vec: Sequence[float],
    *,
    pool: Optional[str] = None,
    limit: int = 5,
    exclude_id: Optional[str] = None,
) -> List[dict]:
    """Crew most structurally similar to `query_vec`, best-first.

    Dispatches to pgvector when enabled, else computes cosine in Python. Each result
    is a crew dict (minus the raw embedding) plus a `similarity` in [0, 1].
    """
    if not query_vec or len(query_vec) != EMBED_DIM:
        return []
    if _pgvector_enabled():
        try:
            return await _find_similar_pgvector(query_vec, pool=pool, limit=limit, exclude_id=exclude_id)
        except Exception:
            log.warning("embeddings.pgvector_failed_fallback", exc_info=True)
    return await _find_similar_python(query_vec, pool=pool, limit=limit, exclude_id=exclude_id)


async def _find_similar_pgvector(
    query_vec: Sequence[float], *, pool, limit, exclude_id
) -> List[dict]:
    """Similarity via pgvector's `<=>` (cosine distance). The JSON embedding casts
    to a vector inline, so no dedicated vector column is required."""
    clauses = ["embedding IS NOT NULL"]
    params = {"q": _vec_literal(query_vec), "lim": limit}
    if pool:
        clauses.append("pool = :pool")
        params["pool"] = pool
    if exclude_id:
        clauses.append("crew_id <> :exclude_id")
        params["exclude_id"] = exclude_id
    where = " AND ".join(clauses)
    sql = text(
        f"""
        SELECT crew_id, name, rank, grade, nationality, vessel, port, status, pool,
               1 - ((embedding::text)::vector <=> (:q)::vector) AS similarity
        FROM crew
        WHERE {where}
        ORDER BY (embedding::text)::vector <=> (:q)::vector ASC
        LIMIT :lim
        """
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, params)
        rows = result.mappings().all()
    return [{**dict(r), "similarity": round(float(r["similarity"]), 4)} for r in rows]


async def _find_similar_python(
    query_vec: Sequence[float], *, pool, limit, exclude_id
) -> List[dict]:
    """Fallback similarity: load embeddings and rank by cosine in Python."""
    async with AsyncSessionLocal() as session:
        q = select(Crew)
        if pool:
            q = q.where(Crew.pool == pool)
        rows = (await session.execute(q)).scalars().all()
    scored = []
    for row in rows:
        if exclude_id and row.crew_id == exclude_id:
            continue
        emb = row.embedding or embed_crew(row.to_dict())
        if not emb or len(emb) != EMBED_DIM:
            continue
        sim = cosine(query_vec, emb)
        d = row.to_dict()
        d.pop("embedding", None)
        d["similarity"] = round(sim, 4)
        scored.append(d)
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


async def _invalidate_crew_cache() -> None:
    try:
        from services.cache_service import cache_service
        await cache_service.delete("crew:signon", "crew:signoff")
    except Exception:
        pass
