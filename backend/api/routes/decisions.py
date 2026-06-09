"""
Decision Graph API routes (L4).

Exposes the captured decision traces: the list for the L4 Decision Graph view,
the full trace for a single decision, and a demo-seed endpoint so the view has
data to show before any live workflow has run.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.routes.workflow import get_workflow_service
from database.decision_repository import get_decision, list_audit, list_decisions
from services.decision_trace_service import decision_trace_service

router = APIRouter(prefix="/decisions", tags=["decisions"])


class ReviewRequest(BaseModel):
    """A human reviewer's verdict on a pending_review decision (HITL)."""
    action: str                              # approve | reject | override
    reviewer: Optional[str] = None
    reason: Optional[str] = None             # reason code / category
    comments: Optional[str] = None           # free-text rationale
    evidence: Optional[list] = None          # [{type, label, ref}]
    override_crew_id: Optional[str] = None   # required for action="override"


@router.get("/", response_model=list)
async def get_decisions(limit: int = 50, review_status: Optional[str] = None):
    """Most-recent-first list of captured placement decisions.

    Pass `review_status=pending_review` to fetch just the HITL review queue.
    """
    return await list_decisions(limit=limit, review_status=review_status)


@router.post("/{decision_id}/review", response_model=dict)
async def review_decision(decision_id: str, body: ReviewRequest):
    """Apply a human verdict (approve / reject / override) to a pending decision.

    Resolves the decision, updates the crew pool on a sign-on, records the verdict
    as a precedent, and writes an immutable audit row.
    """
    service = get_workflow_service()
    try:
        updated = await service.apply_human_review(
            decision_id,
            action=body.action,
            reviewer=body.reviewer,
            reason=body.reason,
            comments=body.comments,
            evidence=body.evidence,
            override_crew_id=body.override_crew_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return updated


@router.get("/{decision_id}/audit", response_model=list)
async def get_decision_audit(decision_id: str):
    """Chronological, append-only audit trail for one decision (HITL)."""
    return await list_audit(decision_id)


@router.post("/demo-seed", response_model=dict)
async def seed_demo_decisions():
    """Insert mock decision traces for demoing the L4 view without a live workflow.

    Idempotent: if sample data already exists it is returned for replay rather than
    re-inserted, so repeated calls don't pile up duplicate rows.
    """
    return await decision_trace_service.seed_demo()


@router.delete("/demo-seed", response_model=dict)
async def clear_demo_decisions():
    """Remove ONLY seeded/sample rows (workflow_id LIKE 'demo-%') from the decision
    and precedent stores. Live placements and real precedent history are preserved."""
    return await decision_trace_service.clear_demo()


@router.get("/{decision_id}", response_model=dict)
async def get_decision_trace(decision_id: str):
    """Full trace (query → trajectory → decision → outcome) for one decision."""
    decision = await get_decision(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")
    return decision
