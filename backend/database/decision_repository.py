"""
Decision-trace data-access layer — async, Postgres-backed.

Stores and queries the `decision_traces` table (see decision_orm.DecisionTrace).
Mirrors the style of crew_repository: plain async functions returning dict /
list[dict] / None so call sites just `await` them.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from database.db import AsyncSessionLocal
from database.decision_orm import DecisionTrace


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
) -> Optional[dict]:
    """Stamp the outcome on the decision produced by this workflow.

    Decisions are keyed by workflow_id from the compliance gate (which only knows
    the workflow, not the decision_id). Updates the most recent matching row.
    Returns the updated row, or None if no decision was captured for the workflow.
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
        row.resolved_at = datetime.utcnow()
        await session.commit()
        return row.to_dict()


async def list_decisions(limit: int = 50) -> list[dict]:
    """Most-recent-first list of captured decisions (lightweight — full trace via get)."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(DecisionTrace)
                .order_by(DecisionTrace.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
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
