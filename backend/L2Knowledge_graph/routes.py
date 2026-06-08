"""
L2 Knowledge Graph API — the query interface over the AGE context graph.

Currently exposes the EntityMap dimension (the canonical entity layer). The
OpsMap and OrgMap dimensions will mount additional read endpoints here as they
land; the route prefix (/graph) and response envelope are designed to host all
three without breaking clients.
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
