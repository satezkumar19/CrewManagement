"""Liveness / readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from config import SERVICE_NAME, SERVICE_VERSION
from core.signal import utcnow

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    """Liveness probe + a snapshot of what's wired in.

    Returns the registered connectors and the placeholder bus's event count so a
    Day-1 demo can show the service is up and ingesting.
    """
    state = request.app.state
    connectors = [
        {"name": c.name, "source_system": c.source_system.value}
        for c in getattr(state, "connectors", [])
    ]
    bus = getattr(state, "bus", None)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "time": utcnow().isoformat(),
        "connectors": connectors,
        "bus_events": getattr(bus, "count", None),
    }
