"""
Decision-trace data-access layer — async, Postgres-backed.

Stores and queries the `decision_traces` table (see decision_orm.DecisionTrace).
Mirrors the style of crew_repository: plain async functions returning dict /
list[dict] / None so call sites just `await` them.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import delete, select

from database.db import AsyncSessionLocal
from database.decision_audit_orm import DecisionAudit
from database.decision_orm import DecisionTrace

# Seed/demo rows are stamped with this workflow_id prefix (see seed_demo); live
# captures never use it, so it's a safe filter for "sample data only".
DEMO_PREFIX = "demo-"


async def insert_decision(record: dict) -> dict:
    """Insert one captured decision. `record` keys map to DecisionTrace columns.

    On a duplicate decision_id the existing row is returned unchanged (capture is
    best-effort and may be retried), so this never raises on re-capture.
    """
    async with AsyncSessionLocal() as session:
        existing = await session.get(DecisionTrace, record["decision_id"])
        if existing is not None:
            return existing.to_dict()
        row = DecisionTrace(**record)
        session.add(row)
        await session.commit()
        return row.to_dict()


async def update_outcome_by_workflow(
    workflow_id: str,
    *,
    outcome_status: str,
    compliance_status: Optional[str] = None,
    compliance_score: Optional[float] = None,
    outcome_reasons: Optional[list] = None,
    attempts: Optional[list] = None,
    chosen_crew: Optional[dict] = None,
    chosen_crew_id: Optional[str] = None,
) -> Optional[dict]:
    """Stamp the outcome on the decision produced by this workflow.

    Decisions are keyed by workflow_id from the compliance gate (which only knows
    the workflow, not the decision_id). Updates the most recent matching row.
    Returns the updated row, or None if no decision was captured for the workflow.

    L4 #4 — `attempts` records the rejection-retry journey, and `chosen_crew(_id)`
    overrides the captured top match when a FALLBACK candidate is the one that
    signed on. Resolving the outcome clears `pending_reason`.
    """
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DecisionTrace)
                .where(DecisionTrace.workflow_id == workflow_id)
                .order_by(DecisionTrace.created_at.desc())
            )
        ).scalars().first()
        if row is None:
            return None
        row.outcome_status = outcome_status
        if compliance_status is not None:
            row.compliance_status = compliance_status
        if compliance_score is not None:
            row.compliance_score = compliance_score
        if outcome_reasons is not None:
            row.outcome_reasons = outcome_reasons
        if attempts is not None:
            row.attempts = attempts
        if chosen_crew is not None:
            row.chosen_crew = chosen_crew
        if chosen_crew_id is not None:
            row.chosen_crew_id = chosen_crew_id
        row.pending_reason = None  # resolved — no longer pending
        row.resolved_at = datetime.utcnow()
        await session.commit()
        return row.to_dict()


async def update_progress_by_workflow(
    workflow_id: str,
    *,
    attempts: list,
    pending_reason: Optional[str] = None,
) -> Optional[dict]:
    """Persist the in-progress rejection-retry journey WITHOUT resolving the outcome.

    Lets a client that lands on the Decisions tab mid-run (or missed a live event)
    read the current attempt chain from the DB: the row stays `pending` (outcome_status
    and resolved_at are untouched) until `update_outcome_by_workflow` stamps the final
    verdict. Updates the most recent decision for the workflow; returns it, or None.
    """
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DecisionTrace)
                .where(DecisionTrace.workflow_id == workflow_id)
                .order_by(DecisionTrace.created_at.desc())
            )
        ).scalars().first()
        if row is None:
            return None
        row.attempts = attempts
        if pending_reason is not None:
            row.pending_reason = pending_reason
        await session.commit()
        return row.to_dict()


async def request_review_by_workflow(
    workflow_id: str,
    *,
    review_trigger: str,
    pending_reason: str,
    ai_proposal: Optional[dict] = None,
    attempts: Optional[list] = None,
    compliance_status: Optional[str] = None,
    compliance_score: Optional[float] = None,
    outcome_reasons: Optional[list] = None,
) -> Optional[dict]:
    """Mark the workflow's decision as awaiting a human (HITL), leaving it pending.

    Sets review_status='pending_review' and review_trigger (why), and freezes the
    AI's proposal in ai_proposal so a later override can show what changed. The
    outcome_status stays 'pending' — only a human verdict resolves it. Updates the
    most recent decision for the workflow; returns it, or None.
    """
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DecisionTrace)
                .where(DecisionTrace.workflow_id == workflow_id)
                .order_by(DecisionTrace.created_at.desc())
            )
        ).scalars().first()
        if row is None:
            return None
        row.review_status = "pending_review"
        row.review_trigger = review_trigger
        row.pending_reason = pending_reason
        if ai_proposal is not None and not row.ai_proposal:
            row.ai_proposal = ai_proposal
        if attempts is not None:
            row.attempts = attempts
        if compliance_status is not None:
            row.compliance_status = compliance_status
        if compliance_score is not None:
            row.compliance_score = compliance_score
        if outcome_reasons is not None:
            row.outcome_reasons = outcome_reasons
        await session.commit()
        return row.to_dict()


async def apply_review_by_decision(
    decision_id: str,
    *,
    review_status: str,
    decision_source: str,
    outcome_status: str,
    chosen_crew: Optional[dict] = None,
    chosen_crew_id: Optional[str] = None,
    compliance_status: Optional[str] = None,
    compliance_score: Optional[float] = None,
    outcome_reasons: Optional[list] = None,
    reviewed_by: Optional[str] = None,
    review_reason: Optional[str] = None,
    review_comments: Optional[str] = None,
    review_evidence: Optional[list] = None,
) -> Optional[dict]:
    """Stamp a human verdict onto one decision (HITL), resolving it.

    Before applying an override, freezes the current (AI) decision into ai_proposal
    if not already set — so the audit shows what the AI wanted vs what the human chose.
    Clears pending_reason and stamps reviewed_at/resolved_at. Returns the row, or None.
    """
    async with AsyncSessionLocal() as session:
        row = await session.get(DecisionTrace, decision_id)
        if row is None:
            return None
        if not row.ai_proposal:
            row.ai_proposal = {
                "chosen_crew_id": row.chosen_crew_id,
                "chosen_crew": row.chosen_crew,
                "outcome_status": row.outcome_status,
                "compliance_status": row.compliance_status,
                "compliance_score": row.compliance_score,
                "confidence_score": row.confidence_score,
            }
        now = datetime.utcnow()
        row.review_status = review_status
        row.decision_source = decision_source
        row.outcome_status = outcome_status
        if chosen_crew is not None:
            row.chosen_crew = chosen_crew
        if chosen_crew_id is not None:
            row.chosen_crew_id = chosen_crew_id
        if compliance_status is not None:
            row.compliance_status = compliance_status
        if compliance_score is not None:
            row.compliance_score = compliance_score
        if outcome_reasons is not None:
            row.outcome_reasons = outcome_reasons
        row.reviewed_by = reviewed_by
        row.review_reason = review_reason
        row.review_comments = review_comments
        if review_evidence is not None:
            row.review_evidence = review_evidence
        row.pending_reason = None
        row.reviewed_at = now
        row.resolved_at = now
        await session.commit()
        return row.to_dict()


async def insert_audit(
    decision_id: str,
    *,
    actor: Optional[str],
    action: str,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    reason: Optional[str] = None,
    comments: Optional[str] = None,
    evidence: Optional[list] = None,
) -> dict:
    """Append one immutable audit row for a decision state transition. Never updates."""
    async with AsyncSessionLocal() as session:
        row = DecisionAudit(
            audit_id=str(uuid4()),
            decision_id=decision_id,
            ts=datetime.utcnow(),
            actor=actor,
            action=action,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            comments=comments,
            evidence=evidence,
        )
        session.add(row)
        await session.commit()
        return row.to_dict()


async def list_audit(decision_id: str) -> list[dict]:
    """Chronological audit trail for one decision (oldest first)."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(DecisionAudit)
                .where(DecisionAudit.decision_id == decision_id)
                .order_by(DecisionAudit.ts.asc())
            )
        ).scalars().all()
        return [r.to_dict() for r in rows]


async def list_decisions(limit: int = 50, *, review_status: Optional[str] = None) -> list[dict]:
    """Most-recent-first list of captured decisions (lightweight — full trace via get).

    Pass review_status to filter (e.g. 'pending_review' for the HITL review queue).
    """
    async with AsyncSessionLocal() as session:
        query = select(DecisionTrace)
        if review_status is not None:
            query = query.where(DecisionTrace.review_status == review_status)
        query = query.order_by(DecisionTrace.created_at.desc()).limit(limit)
        rows = (await session.execute(query)).scalars().all()
        return [r.to_dict() for r in rows]


async def get_decision(decision_id: str) -> Optional[dict]:
    """Full decision trace by id, or None."""
    async with AsyncSessionLocal() as session:
        row = await session.get(DecisionTrace, decision_id)
        return row.to_dict() if row else None


async def count_decisions() -> int:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(DecisionTrace.decision_id))).all()
        return len(rows)


async def count_demo_decisions() -> int:
    """How many seeded/sample decision rows exist (workflow_id LIKE 'demo-%')."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(DecisionTrace.decision_id).where(
                    DecisionTrace.workflow_id.like(f"{DEMO_PREFIX}%")
                )
            )
        ).all()
        return len(rows)


async def delete_demo_audit() -> int:
    """Delete audit rows belonging to seeded/sample decisions (HITL).

    Audit rows key on decision_id (not workflow_id), so match via the demo decisions.
    MUST run BEFORE delete_demo_decisions() — once the decisions are gone the subquery
    finds nothing. Live audit rows are never matched. Returns the number removed.
    """
    async with AsyncSessionLocal() as session:
        demo_ids = select(DecisionTrace.decision_id).where(
            DecisionTrace.workflow_id.like(f"{DEMO_PREFIX}%")
        )
        result = await session.execute(
            delete(DecisionAudit).where(DecisionAudit.decision_id.in_(demo_ids))
        )
        await session.commit()
        return result.rowcount or 0


async def delete_demo_decisions() -> int:
    """Delete ONLY seeded/sample decision rows (workflow_id LIKE 'demo-%').

    Live captures use a real workflow_id and are never matched, so this can't
    touch real data. Returns the number of rows removed.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(DecisionTrace).where(DecisionTrace.workflow_id.like(f"{DEMO_PREFIX}%"))
        )
        await session.commit()
        return result.rowcount or 0
