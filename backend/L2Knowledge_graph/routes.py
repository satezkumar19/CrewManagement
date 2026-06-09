"""
L2 Knowledge Graph API — the query interface over the AGE context graph.

Exposes the EntityMap dimension (the canonical entity layer) under /graph/... and
the OpsMap dimension (process mining over the crew-change workflow) under
/graph/opsmap/... . The OrgMap dimension will mount here next; the route prefix
(/graph) and response envelope are designed to host all three without breaking
clients.

Backend note: EntityMap endpoints require AGE (the canonical graph lives in AGE).
OpsMap endpoints work under BOTH backends — the process model is mined in Python
from the captured event log, so it returns data even when GRAPH_BACKEND=fallback;
only /opsmap/persist (writing the model into AGE) needs the AGE backend.
"""
import time

from fastapi import APIRouter, HTTPException, Query

from L2Knowledge_graph.entity_map import (
    ENTITY_EDGES,
    ENTITY_LABELS,
    entity_map_summary,
    facets,
    node_detail,
    search_crew,
    search_subgraph,
    traverse_crew,
)
from L2Knowledge_graph import ops_map
from L2Knowledge_graph.graph_db import GRAPH_NAME, age_enabled

router = APIRouter(prefix="/graph", tags=["graph"])


def _require_age() -> None:
    if not age_enabled():
        raise HTTPException(
            status_code=503,
            detail="Graph backend disabled. Set GRAPH_BACKEND=age and seed the graph "
            "(python -m L2Knowledge_graph.scripts.seed_entity_map) to enable the L2 knowledge graph.",
        )


@router.get("/summary")
async def graph_summary():
    """EntityMap population summary — per-label node counts and per-type edge counts.
    Confirms the dimension is fully loaded (L2 exit criterion: all dimensions populated).
    """
    _require_age()
    summary = await entity_map_summary()
    return {
        "graph": GRAPH_NAME,
        "dimension": "EntityMap",
        "labels": ENTITY_LABELS,
        "edge_types": ENTITY_EDGES,
        **summary,
    }


@router.get("/facets")
async def graph_facets():
    """Distinct ranks / certificates / ports for the query UI's filter dropdowns."""
    _require_age()
    return await facets()


@router.get("/subgraph")
async def graph_subgraph(
    rank: str | None = Query(None),
    certificate: str | None = Query(None),
    port: str | None = Query(None),
    limit: int = Query(12, ge=1, le=40),
):
    """A React-Flow-ready subgraph (nodes + edges) for a crew search — the matched
    crew plus everything they directly connect to. Powers the Standalone Query UI."""
    _require_age()
    started = time.perf_counter()
    result = await search_subgraph(rank=rank, certificate=certificate, port=port, limit=limit)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


@router.get("/node/{node_id}")
async def graph_node(node_id: str):
    """Full detail for one node (properties + incoming/outgoing relationships).
    Called when a node is clicked in the query UI. node_id is the id from the
    /subgraph payload (e.g. 'c105...' / 'n106...')."""
    _require_age()
    detail = await node_detail(node_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"No graph node for id '{node_id}'.")
    return detail


@router.get("/crew/search")
async def crew_search(
    rank: str | None = Query(None, description="Exact crew rank, e.g. 'Chief Officer'"),
    certificate: str | None = Query(None, description="Certificate the crew must hold"),
    port: str | None = Query(None, description="Port the crew is currently at"),
    limit: int = Query(50, ge=1, le=200),
):
    """Crew search by rank + certificate + port (any subset). certificate/port are
    resolved by graph traversal (HOLDS / CURRENTLY_AT), not a flat column filter."""
    _require_age()
    started = time.perf_counter()
    results = await search_crew(rank=rank, certificate=certificate, port=port, limit=limit)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "filters": {"rank": rank, "certificate": certificate, "port": port},
        "count": len(results),
        "elapsed_ms": elapsed_ms,
        "results": results,
    }


@router.get("/crew/{crew_id}/traverse")
async def crew_traverse(crew_id: str, max_hops: int = Query(2, ge=1, le=4)):
    """Full relationship traversal of one crew member's neighbourhood (multi-hop:
    Crew → Vessel → Port, Crew → Contract → Vessel, …)."""
    _require_age()
    started = time.perf_counter()
    result = await traverse_crew(crew_id, max_hops=max_hops)
    if not result["direct_relationships"]:
        raise HTTPException(
            status_code=404,
            detail=f"No EntityMap node found for crew '{crew_id}' (or it has no relationships).",
        )
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


# ── OpsMap (L2 dimension 2 — process mining) ──────────────────────────────────────
#
# These endpoints DON'T call _require_age(): OpsMap is mined in Python from the
# captured workflow event log, so it serves data under the fallback backend too.
# They return empty (not 503) structures when no workflows have run yet.


@router.get("/opsmap/summary")
async def opsmap_summary():
    """OpsMap population summary — cases mined, distinct activities/transitions,
    variant count, conformance rate, average cycle time."""
    return {"graph": GRAPH_NAME, "backend": "age" if age_enabled() else "fallback", **ops_map.ops_map_summary()}


@router.get("/opsmap/process")
async def opsmap_process():
    """The mined directly-follows process graph (React-Flow-ready nodes + edges with
    frequency and average duration) — the OpsMap 'process model' view. This is the
    discovered crew-change flow: how work ACTUALLY moved, not the documented path."""
    started = time.perf_counter()
    graph = ops_map.build_process_graph()
    graph["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return graph


@router.get("/opsmap/reference")
async def opsmap_reference():
    """The reference (normative) crew-change process model — the DESIGNED flow,
    independent of mined data. Renders a process map even before any workflow has run,
    and gives the discovered model (/opsmap/process) something to be compared against.
    Same envelope as /opsmap/process; nodes carry an `actor`, edges a `kind`."""
    return ops_map.reference_process_model()


@router.get("/opsmap/variants")
async def opsmap_variants():
    """The distinct end-to-end paths cases took, ranked by frequency (happy path vs
    rejection vs failure), with case counts, percentages and average cycle time."""
    return ops_map.process_variants()


@router.get("/opsmap/bottlenecks")
async def opsmap_bottlenecks(limit: int = Query(5, ge=1, le=20)):
    """The slowest handoffs in the process — where crew-change work waits longest."""
    return ops_map.bottlenecks(limit=limit)


@router.get("/opsmap/conformance")
async def opsmap_conformance():
    """How many cases followed the intended crew-change path, and where deviations
    occurred (treating the 3 parallel specialists as order-insensitive)."""
    return ops_map.conformance()


@router.get("/opsmap/cases")
async def opsmap_cases():
    """Per-case records mined from the event log — each crew-change case with the
    actual data behind it (who signed off, who was signed on or rejected/failed and
    why, compliance score, cycle time) plus the ordered steps with their details."""
    return ops_map.process_cases()


@router.post("/opsmap/persist")
async def opsmap_persist():
    """Persist the mined process model into the AGE `maritime` graph as
    (:Activity)-[:NEXT]->(:Activity) edges (overlay on EntityMap). Requires AGE."""
    if not age_enabled():
        raise HTTPException(
            status_code=503,
            detail="Persisting the OpsMap model to AGE needs GRAPH_BACKEND=age. The "
            "OpsMap API itself works under fallback without persisting.",
        )
    return await ops_map.persist_process_model()
