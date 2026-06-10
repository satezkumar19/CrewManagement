"""Unified L2 graph ingress + read views — ``/api/v1/graph/...``.

The wire counterpart of the in-process sink: an external producer (another L1
node, or a backfill job) POSTs unified :class:`~l2.record.L2Record` envelopes and
L2 fans each record's facets out to every map via :class:`~l2.maps.L2Router`.
Read endpoints expose what the maps now hold.

  POST /api/v1/graph/records            — ingest L2Record(s); route facets
  GET  /api/v1/graph/maps               — combined stats for Org/Entity/Ops maps
  GET  /api/v1/graph/entities           — EntityMap node/edge snapshot
  GET  /api/v1/graph/opsmap/cases       — OpsMap per-case event logs + variants
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from l2.record import L2Record

router = APIRouter(prefix="/api/v1/graph", tags=["l2-graph"])


class RecordsIn(BaseModel):
    """Accept a single record (``record``) or a batch (``records``)."""

    record: L2Record | None = None
    records: list[L2Record] = Field(default_factory=list)

    def all(self) -> list[L2Record]:
        out = list(self.records)
        if self.record is not None:
            out.append(self.record)
        return out


def _router(request: Request):
    r = getattr(request.app.state, "l2_router", None)
    if r is None:
        # maps are only wired for the broadcast/in-memory bus (see app.py)
        from fastapi import HTTPException
        raise HTTPException(503, "L2 maps not enabled for this bus")
    return r


@router.post("/records")
async def ingest_records(body: RecordsIn, request: Request) -> dict[str, Any]:
    """Ingest L2Record(s) and route every facet to its map. Idempotent."""
    router_ = _router(request)
    recs = body.all()
    totals = {"org": 0, "entity": 0, "ops": 0}
    for rec in recs:
        applied = router_.route(rec)
        for k in totals:
            totals[k] += applied.get(k, 0)
    return {"received": len(recs), **totals, "maps": router_.stats()}


@router.get("/maps")
async def maps_stats(request: Request) -> dict[str, Any]:
    return _router(request).stats()


@router.get("/entities")
async def entities_snapshot(request: Request, nodes: int = 400, edges: int = 800) -> dict[str, Any]:
    return _router(request).entitymap.snapshot(limit_nodes=nodes, limit_edges=edges)


@router.get("/opsmap/cases")
async def opsmap_cases(request: Request, limit: int = 50) -> dict[str, Any]:
    return _router(request).opsmap.snapshot(limit=limit)
