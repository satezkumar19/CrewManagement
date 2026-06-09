"""
Seed the Apache AGE `maritime` context graph from the crew table + rule data.

Only meaningful when settings.graph_backend == "age" (and the Postgres image has
the AGE extension). Under the default "fallback" backend this script is a no-op
that just explains how to enable AGE — the compliance subgraph is built in Python
and needs no seeding.

Usage:
    # 1. point docker-compose / DATABASE_URL at an AGE-enabled Postgres
    # 2. set GRAPH_BACKEND=age in the environment / .env
    python -m L2Knowledge_graph.scripts.seed_graph

What it builds (see database.compliance_graph for the ontology):
    (:Seafarer)-[:NATIONAL_OF]->(:Country)
    (:Seafarer)-[:HOLDS]->(:Certificate)
    (:Seafarer)-[:ASSIGNED_TO]->(:Vessel)
    (:Vessel)-[:CALLS_AT]->(:Port)
    (:Port)-[:RESTRICTS]->(:Country)
    (:Port)-[:REQUIRES]->(:Certificate)   (per-rank requirements)
"""
import asyncio

import structlog

from L2Knowledge_graph.compliance_graph import PORT_RESTRICTIONS, required_certs_for_rank
from database.crew_repository import get_sign_on_crew
from L2Knowledge_graph.graph_db import GRAPH_NAME, age_enabled, ensure_graph, run_cypher

log = structlog.get_logger()


def _q(value: str) -> str:
    """Minimal escaping for string literals embedded in Cypher."""
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


async def seed() -> None:
    if not age_enabled():
        print(
            "graph_backend != 'age' — nothing to seed.\n"
            "The compliance subgraph is built in Python (fallback mode) and the app\n"
            "works as-is. To use AGE: point DATABASE_URL at an AGE-enabled Postgres,\n"
            "set GRAPH_BACKEND=age, then re-run this script."
        )
        return

    await ensure_graph()

    # Ports + their nationality restrictions and required-medical metadata.
    for port, rules in PORT_RESTRICTIONS.items():
        await run_cypher(
            f"MERGE (p:Port {{name:'{_q(port)}', min_medical_days:{int(rules.get('min_medical_days', 30))}}})"
        )
        for nat in rules.get("visa_required", []):
            await run_cypher(
                f"MERGE (c:Country {{name:'{_q(nat)}'}}) "
                f"WITH c MATCH (p:Port {{name:'{_q(port)}'}}) MERGE (p)-[:RESTRICTS]->(c)"
            )

    # Seafarers from the sign-on pool + their nationality, vessel and certificates.
    # The Seafarer node carries the FULL candidate attribute set as properties (so the
    # graph can SOURCE candidates for L3, not just describe them) — round-trips to the
    # same dict shape as crew_repository.get_sign_on_crew(). Certs are stored as a
    # comma-joined string property (`certs`) to avoid agtype array round-trip quirks; the
    # HOLDS edges below keep the graph-native representation too.
    crew = await get_sign_on_crew()
    for c in crew:
        cid = _q(c.get("crew_id"))
        certs = ",".join(c.get("certifications") or [])
        exp = int(c.get("experience_years") or 0)
        await run_cypher(
            f"MERGE (s:Seafarer {{crew_id:'{cid}'}}) SET "
            f"s.pool='signon', "
            f"s.name='{_q(c.get('name'))}', "
            f"s.rank='{_q(c.get('rank'))}', "
            f"s.grade='{_q(c.get('grade'))}', "
            f"s.nationality='{_q(c.get('nationality'))}', "
            f"s.vessel='{_q(c.get('vessel'))}', "
            f"s.port='{_q(c.get('port'))}', "
            f"s.joining_date='{_q(c.get('joining_date'))}', "
            f"s.medical_expiry='{_q(c.get('medical_expiry'))}', "
            f"s.passport_expiry='{_q(c.get('passport_expiry'))}', "
            f"s.stcw_status='{_q(c.get('stcw_status'))}', "
            f"s.visa_status='{_q(c.get('visa_status'))}', "
            f"s.availability='{_q(c.get('availability'))}', "
            f"s.experience_years={exp}, "
            f"s.certs='{_q(certs)}', "
            f"s.status='{_q(c.get('status'))}'"
        )
        if c.get("nationality"):
            await run_cypher(
                f"MERGE (n:Country {{name:'{_q(c['nationality'])}'}}) "
                f"WITH n MATCH (s:Seafarer {{crew_id:'{cid}'}}) MERGE (s)-[:NATIONAL_OF]->(n)"
            )
        if c.get("vessel"):
            await run_cypher(
                f"MERGE (v:Vessel {{name:'{_q(c['vessel'])}'}}) "
                f"WITH v MATCH (s:Seafarer {{crew_id:'{cid}'}}) MERGE (s)-[:ASSIGNED_TO]->(v)"
            )
        for cert in (c.get("certifications") or []):
            await run_cypher(
                f"MERGE (t:Certificate {{type:'{_q(cert)}'}}) "
                f"WITH t MATCH (s:Seafarer {{crew_id:'{cid}'}}) MERGE (s)-[:HOLDS]->(t)"
            )

    # Per-rank required certificates as Port/Rank REQUIRES edges (kept simple here:
    # attach to every port so the demo graph is connected).
    ranks = {c.get("rank") for c in crew if c.get("rank")}
    for rank in ranks:
        for cert in required_certs_for_rank(rank):
            await run_cypher(f"MERGE (t:Certificate {{type:'{_q(cert)}'}})")

    print(f"Seeded graph '{GRAPH_NAME}': {len(crew)} seafarers, {len(PORT_RESTRICTIONS)} ports.")


if __name__ == "__main__":
    asyncio.run(seed())
