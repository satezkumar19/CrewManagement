"""
L3 Intelligence Graph API.

POST /api/v1/intelligence/match
    Run the Supervisor + 3 investigators for a sign-off and return the top-3 ranked
    replacement candidates (with rationale) plus the operator notifications sent.
    Events stream live over the existing WebSocket (`intel_*` types).
"""
import asyncio
from typing import Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import structlog

from agents.intelligence import IntelligenceSupervisor, context_from_signoff_crew
from agents.intelligence.schemas import SignOffContext
from api.websockets.workflow_ws import manager
from config import settings
from database.crew_repository import get_crew_by_id, update_crew

log = structlog.get_logger()

router = APIRouter(prefix="/intelligence", tags=["intelligence (L3)"])

# Strong refs to in-flight background enrichment tasks so they aren't GC'd mid-run.
_enrichment_tasks: Set[asyncio.Task] = set()


class MatchByCrewRequest(BaseModel):
    crew_id: str                      # the departing (sign-off) crew member
    contract_period_months: int = 6
    top_n: int = 3
    workflow_id: Optional[str] = None


class SignOnRequest(BaseModel):
    crew_id: str                      # the selected (rank-1) replacement candidate
    score: Optional[float] = None     # fused L3 score, recorded as the match score
    reason: Optional[str] = None      # top rationale, recorded as the match reason
    vessel: Optional[str] = None
    workflow_id: Optional[str] = None


class MatchByContextRequest(BaseModel):
    vacated_rank: str
    vacated_grade: Optional[str] = None
    vessel: Optional[str] = None
    port: Optional[str] = None
    contract_period_months: int = 6
    top_n: int = 3
    workflow_id: Optional[str] = None


async def _broadcast(event_type, agent_name, data):
    await manager.broadcast({
        "event_type": event_type, "agent_name": agent_name, "data": data,
    })


def _crew_provider():
    """Candidate source. Under GRAPH_BACKEND=age the pool comes from the L2 AGE graph's
    (:Seafarer) nodes; otherwise the supervisor's default (relational `crew` table)."""
    if settings.graph_backend == "age":
        from database.crew_graph import get_sign_on_crew_from_graph
        return get_sign_on_crew_from_graph
    return None


def _supervisor() -> IntelligenceSupervisor:
    """The authoritative, SLO-meeting response always comes from the deterministic
    supervisor (~ms; <2s first-token / <10s full). When INTEL_BACKEND=managed, the real
    Managed-Agents coordinator + 3 sub-agents run in the BACKGROUND to stream reasoning
    (see _schedule_enrichment) — they enrich the UI without blocking the response.

    Candidates come from the L2 graph when GRAPH_BACKEND=age (see _crew_provider)."""
    return IntelligenceSupervisor(event_callback=_broadcast, crew_provider=_crew_provider())


def _schedule_enrichment(ctx: SignOffContext) -> None:
    """Fire-and-forget the Managed-Agents narration when the managed backend is enabled.
    Streams intel_agent_message events over the WS after the fast response has returned."""
    if settings.intel_backend != "managed" or not settings.managed_l3_coordinator_agent_id:
        return
    from agents.intelligence.managed_supervisor import stream_managed_narration
    task = asyncio.create_task(stream_managed_narration(ctx, _broadcast, crew_provider=_crew_provider()))
    _enrichment_tasks.add(task)
    task.add_done_callback(_enrichment_tasks.discard)


@router.post("/match", response_model=dict)
async def match_for_signoff(req: MatchByCrewRequest):
    """Find the top-N replacements for a departing crew member (by crew_id)."""
    crew = await get_crew_by_id(req.crew_id, pool="signoff")
    if not crew:
        raise HTTPException(status_code=404, detail=f"Sign-off crew {req.crew_id} not found")
    ctx = context_from_signoff_crew(
        crew, contract_period_months=req.contract_period_months, workflow_id=req.workflow_id
    )
    result = await _supervisor().find_replacements(ctx, top_n=req.top_n)
    _schedule_enrichment(ctx)  # managed agents stream reasoning in the background
    return result.to_dict()


@router.post("/match-context", response_model=dict)
async def match_for_context(req: MatchByContextRequest):
    """Find the top-N replacements for an explicit vacancy context (no crew lookup)."""
    ctx = SignOffContext(
        vacated_rank=req.vacated_rank,
        vacated_grade=req.vacated_grade,
        vessel=req.vessel,
        port=req.port,
        contract_period_months=req.contract_period_months,
        workflow_id=req.workflow_id,
    )
    result = await _supervisor().find_replacements(ctx, top_n=req.top_n)
    _schedule_enrichment(ctx)  # managed agents stream reasoning in the background
    return result.to_dict()


@router.post("/sign-on", response_model=dict)
async def sign_on_candidate(req: SignOnRequest):
    """Sign on a selected replacement candidate (the rank-1 pick of an L3 match).

    Moves the crew member from the sign-on pool to the onboard (sign-off) pool —
    modelling them boarding the vessel — and records the L3 match score/reason. The
    Redis crew-list caches are invalidated by ``update_crew``. Two events are
    broadcast: ``intel_signed_on`` (for the L3 trace/UI) and ``crew_updated`` (so the
    dashboard's crew lists re-fetch via SWR).
    """
    crew = await get_crew_by_id(req.crew_id, pool="signon")
    if not crew:
        raise HTTPException(
            status_code=404,
            detail=f"Sign-on candidate {req.crew_id} not found (already signed on?)",
        )

    updated = await update_crew(
        req.crew_id,
        pool="signoff",
        status="Signed On",
        match_score=req.score,
        match_reason=req.reason,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Crew {req.crew_id} not found")

    payload = {
        "crew_id": updated["crew_id"],
        "name": updated.get("name"),
        "rank": updated.get("rank"),
        "vessel": req.vessel or updated.get("vessel"),
        "score": req.score,
        "reason": req.reason,
        "workflow_id": req.workflow_id,
    }
    await manager.broadcast({
        "event_type": "intel_signed_on",
        "agent_name": "Intelligence Supervisor",
        "data": payload,
    })
    # Nudge the dashboard to revalidate both crew lists (page.tsx watches this).
    await manager.broadcast({
        "event_type": "crew_updated",
        "agent_name": "Intelligence Supervisor",
        "data": {"crew_id": updated["crew_id"]},
    })

    return {"status": "signed_on", "pool": "signoff", **payload}
