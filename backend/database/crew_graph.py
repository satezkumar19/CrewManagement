"""
Graph-sourced crew access — the L2 EntityMap supplying L3's candidate pool.

When `GRAPH_BACKEND=age`, L3 sources its candidate entities from the Apache AGE
`maritime` graph's `(:Seafarer)` nodes (seeded by `scripts/seed_graph.py`) instead of
the relational `crew` table. The returned shape is IDENTICAL to
`crew_repository.get_sign_on_crew()` (the `Crew.to_dict()` shape), so the L3
investigators, ranking, fit graph and UI are unchanged — only the source differs.

Falls back to an empty list under the non-AGE backend, so callers can degrade to the
relational pool.
"""
from typing import Any, Dict, List

import structlog

from L2Knowledge_graph.graph_db import age_enabled, run_cypher

log = structlog.get_logger()


def _split_certs(value: Any) -> List[str]:
    if not value:
        return []
    return [c for c in str(value).split(",") if c]


async def get_sign_on_crew_from_graph() -> List[Dict[str, Any]]:
    """Read the sign-on candidate pool from the L2 AGE graph (`(:Seafarer {pool:'signon'})`).

    Returns the same list[dict] shape as crew_repository.get_sign_on_crew(); [] when AGE
    is not the active backend.
    """
    if not age_enabled():
        return []
    # properties(s) returns the node's property map as a clean agtype map (a whole-vertex
    # RETURN would come back as a `…::vertex`-suffixed value that json can't parse).
    rows = await run_cypher("MATCH (s:Seafarer {pool:'signon'}) RETURN properties(s)")
    out: List[Dict[str, Any]] = []
    for r in rows:
        p = r if isinstance(r, dict) else {}
        if not p.get("crew_id"):
            continue
        out.append({
            "crew_id": p.get("crew_id"),
            "name": p.get("name"),
            "rank": p.get("rank"),
            "grade": p.get("grade"),
            "nationality": p.get("nationality"),
            "vessel": p.get("vessel"),
            "port": p.get("port"),
            "joining_date": p.get("joining_date") or None,
            "medical_expiry": p.get("medical_expiry") or None,
            "passport_expiry": p.get("passport_expiry") or None,
            "stcw_status": p.get("stcw_status"),
            "visa_status": p.get("visa_status"),
            "availability": p.get("availability") or None,
            "experience_years": p.get("experience_years"),
            "certifications": _split_certs(p.get("certs")),
            "match_score": None,
            "match_reason": None,
            "status": p.get("status"),
        })
    # Stable order by crew_id (matches the relational repo's ORDER BY crew_id).
    out.sort(key=lambda c: c.get("crew_id") or "")
    log.info("crew_graph.loaded", count=len(out))
    return out
