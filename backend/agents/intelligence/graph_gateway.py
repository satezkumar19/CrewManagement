"""
L3 → L2 gateway — the single seam through which the Intelligence-Graph investigators
read the L2 Knowledge / Compliance Graph.

It speaks to L2 via L2's own public surface (`database.graph_db` for openCypher and
`database.compliance_graph` for the rule data the graph is seeded from), so it works
under BOTH L2 backends and the investigators never need to know which one ran:

  * graph_backend="age"      → openCypher over the Apache AGE `maritime` graph
  * graph_backend="fallback" → the same facts from the Python rule data L2 seeds from

This mirrors how the Compliance Agent already consumes L2 (same module, same shape).
"""
from typing import Any, Dict, List

import structlog

from L2Knowledge_graph.compliance_graph import (
    PORT_RESTRICTIONS,
    build_compliance_subgraph,
    required_certs_for_rank,
)
from L2Knowledge_graph.graph_db import age_enabled, run_cypher

log = structlog.get_logger()


def backend() -> str:
    return "age" if age_enabled() else "fallback"


def _q(v: str) -> str:
    return str(v or "").replace("\\", "\\\\").replace("'", "\\'")


async def port_restriction_facts(port: str) -> Dict[str, Any]:
    """L2's signature multi-hop fact: which nationalities are restricted at `port`
    (Port-[:RESTRICTS]->Country) plus the port's minimum medical validity.

    age mode reads it from the graph; fallback reads the rule data the graph is
    seeded from. Same shape either way.
    """
    if age_enabled():
        try:
            rows = await run_cypher(
                f"MATCH (p:Port {{name:'{_q(port)}'}})-[:RESTRICTS]->(c:Country) RETURN c.name"
            )
            restricted = [r for r in rows if isinstance(r, str)]
            med = await run_cypher(
                f"MATCH (p:Port {{name:'{_q(port)}'}}) RETURN p.min_medical_days"
            )
            min_medical = int(med[0]) if med and isinstance(med[0], (int, float)) else 30
            return {"restricted_nationalities": restricted, "min_medical_days": min_medical, "backend": "age"}
        except Exception as exc:  # noqa: BLE001 - fall back to rule data on any AGE error
            log.warning("graph_gateway.age_query_failed", port=port, error=str(exc))
    r = PORT_RESTRICTIONS.get(port, {})
    return {
        "restricted_nationalities": list(r.get("visa_required", [])),
        "min_medical_days": r.get("min_medical_days", 30),
        "backend": "fallback",
    }


def safety_certs_for_rank(rank: str) -> List[str]:
    """L2's REQUIRES-edge safety certificates for a rank (Port/Rank-[:REQUIRES]->Certificate)."""
    return required_certs_for_rank(rank)


def compliance_view(crew: Dict[str, Any], port: str) -> Dict[str, Any]:
    """The candidate's full L2 compliance subgraph (nodes/edges/findings/verdict) —
    the same structure the Compliance Agent and the React-Flow panel use."""
    return build_compliance_subgraph(crew, port, backend=backend())
