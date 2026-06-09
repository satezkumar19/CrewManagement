"""
Decision Trace Service (L4) — captures L3 placement decisions.

L3 (Master Agent + specialists) makes the placement decision and leaves its
working scattered across the WorkflowState: the query context, the chosen crew,
the ranked alternatives, and the full agent trajectory. This service is a
READ-ONLY consumer of that state: when sign-off orchestration finishes it
ASSEMBLES those pieces into one structured Decision record, PERSISTS it
(decision_traces table), and BROADCASTS a `decision_logged` event so the L4
Decision Graph view updates live. When the compliance gate later resolves, it
stamps the OUTCOME (signed_on / rejected) onto the same record.

Capture is best-effort: any failure here is swallowed and logged so it can never
break a workflow turn (mirrors the skill-sweep convention in managed/client.py).
"""
import json
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from database.decision_repository import (
    apply_review_by_decision,
    count_demo_decisions,
    delete_demo_audit,
    delete_demo_decisions,
    insert_audit,
    insert_decision,
    list_decisions,
    request_review_by_workflow,
    update_outcome_by_workflow,
    update_progress_by_workflow,
)
from database.precedent_repository import delete_demo_precedents
from database.models import WorkflowState
from services.precedent_service import precedent_service

log = structlog.get_logger()

# broadcast(event_type, agent_name, data) -> Awaitable — the same callback the
# WorkflowService uses to relay to the WebSocket manager. Passed in at call time.
Broadcast = Callable[[str, str, Dict[str, Any]], Awaitable[None]]

_MAX_IO_CHARS = 600  # cap tool input/output blobs stored per trajectory step


def _truncate(value: Any) -> Any:
    """JSON-safe, length-capped representation of a tool input/output for the trace."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)
    return text if len(text) <= _MAX_IO_CHARS else text[:_MAX_IO_CHARS] + "…"


class DecisionTraceService:
    async def capture(
        self, workflow: WorkflowState, broadcast: Optional[Broadcast] = None
    ) -> Optional[dict]:
        """Assemble + persist the decision trace for a finished sign-off orchestration.

        Returns the stored record (dict), or None if there was no match to record
        or capture failed. Never raises.
        """
        try:
            if not (workflow.matched_crew_id or (workflow.matched_crew or {}).get("crew_id")):
                log.info("decision.capture.skipped_no_match", workflow_id=workflow.workflow_id)
                return None
            record = self._assemble(workflow)
            stored = await insert_decision(record)
            log.info(
                "decision.captured",
                decision_id=stored["decision_id"],
                workflow_id=workflow.workflow_id,
                chosen=stored.get("chosen_crew_id"),
            )
            if broadcast:
                await self._safe_broadcast(broadcast, "decision_logged", "Decision Graph", {
                    "workflow_id": workflow.workflow_id,
                    "decision_id": stored["decision_id"],
                    "chosen_crew": stored.get("chosen_crew"),
                    "confidence_score": stored.get("confidence_score"),
                    "alternatives_count": len(stored.get("alternatives", [])),
                    "trajectory_steps": len(stored.get("trajectory", [])),
                    "message": (
                        f"Decision logged: {stored.get('chosen_crew', {}).get('name', 'candidate')} "
                        f"selected ({stored.get('confidence_score')}% confidence)"
                    ),
                })
            return stored
        except Exception:
            log.warning("decision.capture.failed", workflow_id=workflow.workflow_id, exc_info=True)
            return None

    async def record_progress(
        self,
        workflow_id: str,
        *,
        attempts: list,
        pending_reason: Optional[str] = None,
    ) -> Optional[dict]:
        """Persist the in-progress attempt chain mid-retry, leaving the outcome pending.

        L4 #4 — so a client that opens the Decisions tab while a sign-off is still
        running (or that missed a live WebSocket event) reads the current reject→retry
        journey from the DB instead of a stale lone 'pending' node. Best-effort; never
        raises and never broadcasts (the live events already carry the same data).
        """
        try:
            return await update_progress_by_workflow(
                workflow_id, attempts=attempts, pending_reason=pending_reason
            )
        except Exception:
            log.warning("decision.progress.failed", workflow_id=workflow_id, exc_info=True)
            return None

    async def record_outcome(
        self,
        workflow_id: str,
        *,
        outcome_status: str,
        compliance_status: Optional[str] = None,
        compliance_score: Optional[float] = None,
        outcome_reasons: Optional[list] = None,
        attempts: Optional[list] = None,
        chosen_crew: Optional[dict] = None,
        chosen_crew_id: Optional[str] = None,
        broadcast: Optional[Broadcast] = None,
    ) -> Optional[dict]:
        """Stamp the outcome (signed_on | rejected) on the workflow's decision. Never raises.

        L4 #4 — `attempts` is the rejection-retry journey; `chosen_crew(_id)` overrides
        the captured top match when a fallback candidate is the one that signed on.
        """
        try:
            updated = await update_outcome_by_workflow(
                workflow_id,
                outcome_status=outcome_status,
                compliance_status=compliance_status,
                compliance_score=compliance_score,
                outcome_reasons=outcome_reasons,
                attempts=attempts,
                chosen_crew=chosen_crew,
                chosen_crew_id=chosen_crew_id,
            )
            if updated is None:
                log.info("decision.outcome.no_decision", workflow_id=workflow_id)
                return None
            log.info(
                "decision.outcome.recorded",
                decision_id=updated["decision_id"],
                outcome=outcome_status,
            )
            if broadcast:
                await self._safe_broadcast(broadcast, "decision_outcome", "Decision Graph", {
                    "workflow_id": workflow_id,
                    "decision_id": updated["decision_id"],
                    "outcome_status": outcome_status,
                    "compliance_status": compliance_status,
                    "compliance_score": compliance_score,
                    "message": (
                        f"Decision outcome: {updated.get('chosen_crew', {}).get('name', 'candidate')} "
                        f"→ {outcome_status}"
                    ),
                })
            return updated
        except Exception:
            log.warning("decision.outcome.failed", workflow_id=workflow_id, exc_info=True)
            return None

    # ── Human-in-the-loop review (L4 HITL) ──────────────────────────────────────

    async def request_review(
        self,
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
        """Pause the workflow's decision for a human (review_status='pending_review').

        Persists the gate + the AI's frozen proposal and writes an audit row. The
        caller (WorkflowService) broadcasts the `review_requested` event, since it
        carries the candidate context. Best-effort; never raises.
        """
        try:
            updated = await request_review_by_workflow(
                workflow_id,
                review_trigger=review_trigger,
                pending_reason=pending_reason,
                ai_proposal=ai_proposal,
                attempts=attempts,
                compliance_status=compliance_status,
                compliance_score=compliance_score,
                outcome_reasons=outcome_reasons,
            )
            if updated is None:
                log.info("decision.review.no_decision", workflow_id=workflow_id)
                return None
            await insert_audit(
                updated["decision_id"],
                actor="system",
                action="review_requested",
                from_state="pending",
                to_state="pending_review",
                reason=review_trigger,
                comments=pending_reason,
            )
            log.info(
                "decision.review.requested",
                decision_id=updated["decision_id"], trigger=review_trigger,
            )
            return updated
        except Exception:
            log.warning("decision.request_review.failed", workflow_id=workflow_id, exc_info=True)
            return None

    async def apply_review(
        self,
        decision_id: str,
        *,
        action: str,                       # approve | reject | override
        reviewer: Optional[str],
        outcome_status: str,               # signed_on | rejected
        decision_source: str,              # human | ai_then_human
        chosen_crew: Optional[dict] = None,
        chosen_crew_id: Optional[str] = None,
        compliance_status: Optional[str] = None,
        compliance_score: Optional[float] = None,
        outcome_reasons: Optional[list] = None,
        reason: Optional[str] = None,
        comments: Optional[str] = None,
        evidence: Optional[list] = None,
        broadcast: Optional[Broadcast] = None,
    ) -> Optional[dict]:
        """Stamp a human verdict onto a decision, resolving it, and audit it.

        Writes the review fields + an immutable audit row, then broadcasts
        `decision_reviewed` so the Decision Graph resolves live. Best-effort; never
        raises. Crew-pool / precedent side effects are handled by the caller
        (WorkflowService.apply_human_review) so they mirror the automated path.
        """
        try:
            review_status = {
                "approve": "approved", "reject": "rejected", "override": "overridden",
            }.get(action, "approved")
            updated = await apply_review_by_decision(
                decision_id,
                review_status=review_status,
                decision_source=decision_source,
                outcome_status=outcome_status,
                chosen_crew=chosen_crew,
                chosen_crew_id=chosen_crew_id,
                compliance_status=compliance_status,
                compliance_score=compliance_score,
                outcome_reasons=outcome_reasons,
                reviewed_by=reviewer,
                review_reason=reason,
                review_comments=comments,
                review_evidence=evidence,
            )
            if updated is None:
                log.info("decision.review.apply_no_decision", decision_id=decision_id)
                return None
            await insert_audit(
                decision_id,
                actor=reviewer or "reviewer",
                action=f"review_{action}",
                from_state="pending_review",
                to_state=outcome_status,
                reason=reason,
                comments=comments,
                evidence=evidence,
            )
            log.info(
                "decision.review.applied",
                decision_id=decision_id, action=action, outcome=outcome_status,
            )
            if broadcast:
                await self._safe_broadcast(broadcast, "decision_reviewed", "Human Reviewer", {
                    "workflow_id": updated.get("workflow_id"),
                    "decision_id": decision_id,
                    "action": action,
                    "reviewer": reviewer,
                    "reason": reason,
                    "outcome_status": outcome_status,
                    "decision_source": decision_source,
                    "chosen_crew": updated.get("chosen_crew"),
                    "message": (
                        f"Human review — {reviewer or 'reviewer'} {action}d "
                        f"→ {outcome_status.replace('_', ' ')}"
                    ),
                })
            return updated
        except Exception:
            log.warning("decision.apply_review.failed", decision_id=decision_id, exc_info=True)
            return None

    # ── Assembly ────────────────────────────────────────────────────────────────

    def _assemble(self, workflow: WorkflowState) -> dict:
        sign_off = workflow.sign_off_crew or {}
        matched = workflow.matched_crew or {}
        match_result = workflow.crew_match_result or {}
        chosen_id = workflow.matched_crew_id or matched.get("crew_id")

        # Alternatives = ranked candidates that were NOT chosen.
        ranked = match_result.get("ranked_candidates") or []
        alternatives = [c for c in ranked if c.get("crew_id") != chosen_id]

        short_term = (workflow.memory or {}).get("short_term", {})
        reason = short_term.get("reason")
        # Precedent Index (#2): what the lookup returned at the start of this query.
        precedent = short_term.get("precedent") or {}

        return {
            "decision_id": str(uuid.uuid4()),
            "workflow_id": workflow.workflow_id,
            "created_at": datetime.utcnow(),
            "trigger": workflow.trigger,
            "query_context": {
                "departing_crew": {
                    "crew_id": sign_off.get("crew_id"),
                    "name": sign_off.get("name"),
                    "rank": sign_off.get("rank"),
                    "grade": sign_off.get("grade"),
                    "vessel": sign_off.get("vessel"),
                    "port": sign_off.get("port"),
                    "nationality": sign_off.get("nationality"),
                },
                "reason": reason,
            },
            "chosen_crew_id": chosen_id,
            "chosen_crew": {
                "crew_id": chosen_id,
                "name": matched.get("name"),
                "rank": matched.get("rank"),
                "grade": matched.get("grade"),
                "port": matched.get("port"),
                "nationality": matched.get("nationality"),
            },
            "confidence_score": matched.get("confidence_score") or match_result.get("confidence_score"),
            "match_reasons": matched.get("match_reasons", []),
            "alternatives": alternatives,
            "trajectory": self._flatten_trajectory(workflow),
            "is_repeat_query": bool(precedent.get("is_repeat")),
            "consulted_precedents": precedent or None,
            # L4 #3 — how the consulted precedent re-ranked the match (None for a
            # first-time vacancy, where no boost was applied).
            "precedent_feedback": match_result.get("precedent_feedback"),
            "attempts": [],
            "pending_reason": (
                "Awaiting compliance validation — the matched candidate has not yet "
                "been run through the compliance gate."
            ),
            "outcome_status": "pending",
            "session_id": workflow.session_id,
            "total_tokens": workflow.total_tokens,
            "total_cost": workflow.total_cost,
            "cache_read_tokens": workflow.cache_read_tokens,
            "cache_creation_tokens": workflow.cache_creation_tokens,
        }

    def _flatten_trajectory(self, workflow: WorkflowState) -> List[Dict[str, Any]]:
        """Ordered tool-call steps across all agent executions — the 'how it was reached'.

        Each execution contributes an agent header step plus one step per tool call,
        so the trace reads agent → tool → input → output in order.
        """
        steps: List[Dict[str, Any]] = []
        for ex in workflow.agent_executions or []:
            steps.append({
                "kind": "agent",
                "agent_name": ex.agent_name,
                "agent_type": ex.agent_type,
                "status": ex.status.value if hasattr(ex.status, "value") else str(ex.status),
                "confidence_score": ex.confidence_score,
                "tokens_used": ex.tokens_used,
                "duration_ms": ex.duration_ms,
            })
            for tc in ex.tool_calls or []:
                steps.append({
                    "kind": "tool",
                    "agent_name": ex.agent_name,
                    "tool_name": tc.tool_name,
                    "input": _truncate(tc.input),
                    "output": _truncate(tc.output),
                    "duration_ms": tc.duration_ms,
                    "timestamp": tc.timestamp.isoformat() if tc.timestamp else None,
                })
        return steps

    async def _safe_broadcast(
        self, broadcast: Broadcast, event_type: str, agent_name: str, data: Dict[str, Any]
    ) -> None:
        try:
            await broadcast(event_type, agent_name, data)
        except Exception:
            pass

    # ── Demo seeding ──────────────────────────────────────────────────────────────

    async def clear_demo(self) -> dict:
        """Remove ONLY seeded/sample rows (workflow_id LIKE 'demo-%') from both the
        decision-trace and precedent stores. Live placements use a real workflow_id
        and are never matched, so real precedent history is preserved. Never raises."""
        try:
            # Audit rows first (they key on decision_id — must be matched before the
            # demo decisions they point at are deleted).
            await delete_demo_audit()
            await self._delete_demo_crew()
            decisions_removed = await delete_demo_decisions()
            precedents_removed = await delete_demo_precedents()
            log.info(
                "decision.clear_demo",
                decisions=decisions_removed, precedents=precedents_removed,
            )
            return {
                "decisions_removed": decisions_removed,
                "precedents_removed": precedents_removed,
            }
        except Exception:
            log.warning("decision.clear_demo.failed", exc_info=True)
            return {"decisions_removed": 0, "precedents_removed": 0}

    async def seed_demo(self) -> dict:
        """Insert realistic mock decisions so the L4 view has data before any live
        workflow has run. Processed IN ORDER so the Precedent Index builds up: each
        decision consults the precedents recorded by the earlier ones, then (if
        completed) records its own — so a later decision with a repeated vacancy
        profile shows up as a 2nd+ query.

        IDEMPOTENT: if sample data is already present, it is NOT re-inserted (so
        repeated seeding can't pile up duplicate rows); the existing sample set is
        returned for replay instead. Use clear_demo() to remove it first if you want
        a fresh batch."""
        # Ensure the sign-on crew that pending-review fixtures propose actually exist,
        # so a reviewer can APPROVE them end-to-end (idempotent; safe to call on replay).
        await self._ensure_demo_crew()
        if await count_demo_decisions() > 0:
            existing = [
                d for d in await list_decisions(limit=200)
                if (d.get("workflow_id") or "").startswith("demo-")
            ]
            log.info("decision.seed_demo.already_present", count=len(existing))
            return {"seeded": 0, "already_present": True, "decisions": existing}
        seeded = []
        for spec in _DEMO_DECISIONS:
            dep = spec["departing"]
            # Consult against precedents already seeded in THIS pass (and any prior).
            precedent = await precedent_service.consult(
                rank=dep.get("rank"), grade=dep.get("grade"),
                port=dep.get("port"), nationality=dep.get("nationality"),
            )
            record = self._mock_record(spec, precedent)
            stored = await insert_decision(record)
            seeded.append(stored)
            # NOTE: no audit rows are seeded — the decision audit stays EMPTY after a
            # fresh seed. It only fills when a human actually reviews a decision in the
            # app: the first real review (on the pending-review case) writes the first
            # audit entry, and that verdict then becomes a precedent later sign-offs
            # weight up. Seeded placements (AI) still enter the Precedent Index.
            if spec["outcome_status"] in ("signed_on", "rejected"):
                await precedent_service.record_placement(stored)
        log.info("decision.seed_demo", count=len(seeded))
        return {"seeded": len(seeded), "already_present": False, "decisions": seeded}

    async def _ensure_demo_crew(self) -> None:
        """Insert the sign-on crew referenced by pending-review demo fixtures so the
        APPROVE action resolves end-to-end (apply_human_review looks the candidate up in
        the sign-on pool). Idempotent (merge by crew_id); demo-only (CM- ids, which never
        collide with live SNO-/SOF- crew); best-effort. Removed by clear_demo."""
        try:
            from database.crew_orm import Crew
            from database.db import AsyncSessionLocal
            specs = [s for s in _DEMO_DECISIONS if s.get("review_status") == "pending_review"]
            if not specs:
                return
            async with AsyncSessionLocal() as session:
                for s in specs:
                    c = s["chosen"]
                    await session.merge(Crew(
                        crew_id=c["crew_id"], pool="signon", status="Available",
                        name=c.get("name"), rank=c.get("rank"), grade=c.get("grade"),
                        nationality=c.get("nationality"), port=c.get("port"),
                        vessel="Available", availability="Available",
                    ))
                await session.commit()
            # Invalidate the cached sign-on list so the new crew is visible immediately.
            try:
                from services.cache_service import cache_service
                await cache_service.delete("crew:signon", "crew:signoff")
            except Exception:
                pass
        except Exception:
            log.warning("decision.ensure_demo_crew.failed", exc_info=True)

    async def _delete_demo_crew(self) -> None:
        """Remove the demo sign-on crew added by _ensure_demo_crew (by their CM- ids).
        Best-effort; only touches the specific demo fixture candidates."""
        try:
            from sqlalchemy import delete as _delete
            from database.crew_orm import Crew
            from database.db import AsyncSessionLocal
            ids = [s["chosen"]["crew_id"] for s in _DEMO_DECISIONS if s.get("review_status") == "pending_review"]
            if not ids:
                return
            async with AsyncSessionLocal() as session:
                await session.execute(_delete(Crew).where(Crew.crew_id.in_(ids)))
                await session.commit()
            try:
                from services.cache_service import cache_service
                await cache_service.delete("crew:signon", "crew:signoff")
            except Exception:
                pass
        except Exception:
            log.warning("decision.delete_demo_crew.failed", exc_info=True)

    def _mock_record(self, spec: Dict[str, Any], precedent: Dict[str, Any]) -> dict:
        return {
            "decision_id": str(uuid.uuid4()),
            "workflow_id": f"demo-{uuid.uuid4().hex[:8]}",
            "created_at": datetime.utcnow(),
            "trigger": spec["trigger"],
            "query_context": {"departing_crew": spec["departing"], "reason": "Contract completion"},
            "chosen_crew_id": spec["chosen"]["crew_id"],
            "chosen_crew": spec["chosen"],
            "confidence_score": spec["confidence"],
            "match_reasons": spec["match_reasons"],
            "alternatives": spec["alternatives"],
            "trajectory": spec["trajectory"],
            "is_repeat_query": bool(precedent.get("is_repeat")),
            "consulted_precedents": precedent or None,
            "precedent_feedback": spec.get("precedent_feedback"),
            "attempts": spec.get("attempts", []),
            "pending_reason": spec.get("pending_reason") if spec["outcome_status"] == "pending" else None,
            "outcome_status": spec["outcome_status"],
            "compliance_status": spec.get("compliance_status"),
            "compliance_score": spec.get("compliance_score"),
            "outcome_reasons": spec.get("outcome_reasons", []),
            "resolved_at": datetime.utcnow() if spec["outcome_status"] != "pending" else None,
            # ── HITL (L4) — pass-through so seeded review fixtures render the same as
            # a live human-reviewed decision. Defaults keep plain AI fixtures unchanged.
            "decision_source": spec.get("decision_source", "ai"),
            "review_status": spec.get("review_status"),
            "review_trigger": spec.get("review_trigger"),
            "reviewed_by": spec.get("reviewed_by"),
            "reviewed_at": (
                datetime.utcnow()
                if spec.get("review_status") in ("approved", "rejected", "overridden")
                else None
            ),
            "review_reason": spec.get("review_reason"),
            "review_comments": spec.get("review_comments"),
            "review_evidence": spec.get("review_evidence"),
            "ai_proposal": spec.get("ai_proposal"),
            "session_id": f"sess-{uuid.uuid4().hex[:8]}",
            "total_tokens": spec["total_tokens"],
            "total_cost": spec["total_cost"],
            "cache_read_tokens": spec.get("cache_read_tokens", 0),
            "cache_creation_tokens": spec.get("cache_creation_tokens", 0),
        }


# Demo fixtures — shaped exactly like a real captured decision so the L4 view and
# the downstream phases (#2 precedent / #4 patterns) can be demoed without a live
# run. Five decisions with varied outcomes (clear pass, conditional warning,
# rejection, still-pending) so the auto-play walkthrough shows the full spectrum.
_DEMO_DECISIONS: List[Dict[str, Any]] = [
    {
        "trigger": "Sign-off initiated for Rajesh Kumar (CM-1042)",
        "departing": {
            "crew_id": "CM-1042", "name": "Rajesh Kumar", "rank": "Chief Officer",
            "grade": "A", "vessel": "MV Pacific Dawn", "port": "Singapore", "nationality": "Indian",
        },
        "chosen": {
            "crew_id": "CM-2087", "name": "Arjun Menon", "rank": "Chief Officer",
            "grade": "A", "port": "Singapore", "nationality": "Indian",
        },
        "confidence": 92.4,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Singapore", "All documents valid", "12 years experience"],
        "alternatives": [
            {"crew_id": "CM-2150", "name": "Wei Zhang", "rank": "Chief Officer", "confidence_score": 81.0, "match_reasons": ["Exact rank match", "Grade matches"]},
            {"crew_id": "CM-2233", "name": "Carlos Ruiz", "rank": "Chief Officer", "confidence_score": 74.5, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.924, "tokens_used": 0, "duration_ms": 4200},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Chief Officer", "port": "Singapore"}', "output": '{"found": 5}', "duration_ms": 120, "timestamp": None},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "rankCrew", "input": '{"candidates": ["CM-2087", "CM-2150", "CM-2233"]}', "output": '{"ranked_candidates": [{"crew_id": "CM-2087", "confidence_score": 92.4}]}', "duration_ms": 95, "timestamp": None},
        ],
        "outcome_status": "signed_on",
        "compliance_status": "passed",
        "compliance_score": 98.0,
        "total_tokens": 18450,
        "total_cost": 0.214,
        "cache_read_tokens": 12000,
        "cache_creation_tokens": 3200,
    },
    {
        "trigger": "Sign-off initiated for Maria Santos (CM-1108)",
        "departing": {
            "crew_id": "CM-1108", "name": "Maria Santos", "rank": "Second Engineer",
            "grade": "B", "vessel": "MV Atlantic Star", "port": "Rotterdam", "nationality": "Filipino",
        },
        "chosen": {
            "crew_id": "CM-2301", "name": "Diego Cruz", "rank": "Second Engineer",
            "grade": "B", "port": "Rotterdam", "nationality": "Filipino",
        },
        "confidence": 78.9,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Rotterdam"],
        "alternatives": [
            {"crew_id": "CM-2355", "name": "Tom Baker", "rank": "Second Engineer", "confidence_score": 70.0, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.789, "tokens_used": 0, "duration_ms": 3900},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Second Engineer", "port": "Rotterdam"}', "output": '{"found": 2}', "duration_ms": 110, "timestamp": None},
        ],
        "outcome_status": "rejected",
        "compliance_status": "failed",
        "compliance_score": 41.0,
        # Rejection-retry loop (#4): the top match failed, the loop tried the next-best
        # candidate, and that one failed too — alternatives exhausted → final rejection.
        "attempts": [
            {"order": 1, "crew_id": "CM-2301", "name": "Diego Cruz", "rank": "Second Engineer", "compliance_status": "failed", "compliance_score": 41.0, "failures": ["Visa invalid for Rotterdam", "STCW certificate expired"], "warnings": []},
            {"order": 2, "crew_id": "CM-2355", "name": "Tom Baker", "rank": "Second Engineer", "compliance_status": "failed", "compliance_score": 49.0, "failures": ["Medical certificate expired"], "warnings": []},
        ],
        "outcome_reasons": ["All 2 candidates failed compliance — alternatives exhausted", "Visa invalid for Rotterdam", "STCW certificate expired"],
        "total_tokens": 15200,
        "total_cost": 0.176,
        "cache_read_tokens": 9000,
        "cache_creation_tokens": 2800,
    },
    {
        # Success-after-retry (#4): the top match (Sergey Volkov, 88.1) FAILED
        # compliance, so the loop retried the next-best candidate (John Adams, 83.2)
        # — who cleared with a warning and was signed on. The chosen crew is therefore
        # the lower-confidence fallback; the attempts journey explains why.
        "trigger": "Sign-off initiated for Liam O'Brien (CM-1190)",
        "departing": {
            "crew_id": "CM-1190", "name": "Liam O'Brien", "rank": "Master",
            "grade": "A", "vessel": "MV Northern Light", "port": "Houston", "nationality": "Irish",
        },
        "chosen": {
            "crew_id": "CM-2455", "name": "John Adams", "rank": "Master",
            "grade": "A", "port": "Houston", "nationality": "American",
        },
        "confidence": 83.2,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Houston", "Signed on after top candidate failed compliance"],
        "alternatives": [
            {"crew_id": "CM-2410", "name": "Sergey Volkov", "rank": "Master", "confidence_score": 88.1, "match_reasons": ["Exact rank match", "Grade matches", "18 years experience"]},
            {"crew_id": "CM-2478", "name": "Yusuf Demir", "rank": "Master", "confidence_score": 76.0, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.881, "tokens_used": 0, "duration_ms": 4500},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Master", "port": "Houston"}', "output": '{"found": 3}', "duration_ms": 130, "timestamp": None},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "rankCrew", "input": '{"candidates": ["CM-2410", "CM-2455", "CM-2478"]}', "output": '{"ranked_candidates": [{"crew_id": "CM-2410", "confidence_score": 88.1}, {"crew_id": "CM-2455", "confidence_score": 83.2}]}', "duration_ms": 102, "timestamp": None},
        ],
        "attempts": [
            {"order": 1, "crew_id": "CM-2410", "name": "Sergey Volkov", "rank": "Master", "compliance_status": "failed", "compliance_score": 38.0, "failures": ["US visa expired", "Port restriction: Houston requires valid C1/D visa"], "warnings": []},
            {"order": 2, "crew_id": "CM-2455", "name": "John Adams", "rank": "Master", "compliance_status": "warning", "compliance_score": 84.0, "failures": [], "warnings": ["Medical certificate expires in 45 days — renew before next port"]},
        ],
        "outcome_status": "signed_on",
        "compliance_status": "warning",
        "compliance_score": 84.0,
        "outcome_reasons": ["Medical certificate expires in 45 days — renew before next port"],
        "total_tokens": 19800,
        "total_cost": 0.231,
        "cache_read_tokens": 13500,
        "cache_creation_tokens": 3000,
    },
    {
        "trigger": "Sign-off initiated for Chen Wei (CM-1245)",
        "departing": {
            "crew_id": "CM-1245", "name": "Chen Wei", "rank": "Bosun",
            "grade": "C", "vessel": "MV Eastern Wind", "port": "Shanghai", "nationality": "Chinese",
        },
        "chosen": {
            "crew_id": "CM-2520", "name": "Kwame Asante", "rank": "Bosun",
            "grade": "C", "port": "Shanghai", "nationality": "Ghanaian",
        },
        "confidence": 71.3,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Shanghai"],
        "alternatives": [
            {"crew_id": "CM-2566", "name": "Pavel Novak", "rank": "Bosun", "confidence_score": 68.5, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.713, "tokens_used": 0, "duration_ms": 3700},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Bosun", "port": "Shanghai"}', "output": '{"found": 2}', "duration_ms": 105, "timestamp": None},
        ],
        "outcome_status": "pending",
        "pending_reason": (
            "Sign-on not yet confirmed — Kwame Asante was matched but the compliance "
            "gate has not been run, so the placement outcome is still open."
        ),
        "total_tokens": 12100,
        "total_cost": 0.142,
        "cache_read_tokens": 7000,
        "cache_creation_tokens": 2500,
    },
    {
        "trigger": "Sign-off initiated for Fatima Al-Sayed (CM-1302)",
        "departing": {
            "crew_id": "CM-1302", "name": "Fatima Al-Sayed", "rank": "Chief Engineer",
            "grade": "A", "vessel": "MV Desert Pearl", "port": "Dubai", "nationality": "Egyptian",
        },
        "chosen": {
            "crew_id": "CM-2611", "name": "Henrik Larsen", "rank": "Chief Engineer",
            "grade": "A", "port": "Dubai", "nationality": "Danish",
        },
        "confidence": 95.7,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Dubai", "All documents valid", "20 years experience"],
        "alternatives": [
            {"crew_id": "CM-2650", "name": "Raj Patel", "rank": "Chief Engineer", "confidence_score": 86.4, "match_reasons": ["Exact rank match", "Grade matches"]},
            {"crew_id": "CM-2677", "name": "Sofia Rossi", "rank": "Chief Engineer", "confidence_score": 79.9, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.957, "tokens_used": 0, "duration_ms": 4100},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Chief Engineer", "port": "Dubai"}', "output": '{"found": 4}', "duration_ms": 118, "timestamp": None},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "rankCrew", "input": '{"candidates": ["CM-2611", "CM-2650", "CM-2677"]}', "output": '{"ranked_candidates": [{"crew_id": "CM-2611", "confidence_score": 95.7}]}', "duration_ms": 99, "timestamp": None},
        ],
        "outcome_status": "signed_on",
        "compliance_status": "passed",
        "compliance_score": 99.0,
        "total_tokens": 20100,
        "total_cost": 0.236,
        "cache_read_tokens": 14000,
        "cache_creation_tokens": 3100,
    },
    {
        # Repeat of decision #1's vacancy profile (Chief Officer @ Singapore) — so
        # this one consults the Precedent Index and finds Arjun Menon's prior
        # signed-on placement (Indian, 98%). L4 #3: that precedent is fed back into
        # the matching scorer, which boosts the Indian candidate ABOVE the Russian
        # one that led on the base score — re-ranking the winner.
        "trigger": "Sign-off initiated for Nikolai Petrov (CM-1377)",
        "departing": {
            "crew_id": "CM-1377", "name": "Nikolai Petrov", "rank": "Chief Officer",
            "grade": "A", "vessel": "MV Pacific Dawn", "port": "Singapore", "nationality": "Russian",
        },
        "chosen": {
            "crew_id": "CM-2733", "name": "Rohan Nair", "rank": "Chief Officer",
            "grade": "A", "port": "Singapore", "nationality": "Indian",
        },
        "confidence": 93.8,
        "match_reasons": [
            "Exact rank match", "Grade matches", "Same port: Singapore",
            "Precedent: Indian nationals cleared this vacancy before",
        ],
        "alternatives": [
            {"crew_id": "CM-2780", "name": "Aleksei Ivanov", "rank": "Chief Officer", "confidence_score": 84.0, "base_confidence_score": 84.0, "precedent_boost": 0.0, "match_reasons": ["Exact rank match", "Grade matches"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.938, "tokens_used": 0, "duration_ms": 4000},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Chief Officer", "port": "Singapore"}', "output": '{"found": 4}', "duration_ms": 115, "timestamp": None},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "rankCrew", "input": '{"candidates": ["CM-2733", "CM-2780"]}', "output": '{"ranked_candidates": [{"crew_id": "CM-2733", "confidence_score": 93.8}], "precedent_feedback": {"applied": true, "reranked": true}}', "duration_ms": 98, "timestamp": None},
        ],
        "precedent_feedback": {
            "applied": True,
            "top_base_score": 84.0,
            "top_adjusted_score": 93.8,
            "lift": 9.8,
            "reranked": True,
            "base_winner": {"crew_id": "CM-2780", "name": "Aleksei Ivanov"},
            "adjusted_winner": {"crew_id": "CM-2733", "name": "Rohan Nair"},
            "boosted": [
                {"crew_id": "CM-2733", "name": "Rohan Nair", "nationality": "Indian", "boost": 9.8},
            ],
            "rationale": "Prior signed-on: Arjun Menon (Indian, A) cleared at 98%",
        },
        "outcome_status": "signed_on",
        "compliance_status": "passed",
        "compliance_score": 97.0,
        "total_tokens": 17600,
        "total_cost": 0.205,
        "cache_read_tokens": 11800,
        "cache_creation_tokens": 2900,
    },
    {
        # HITL — a CONDITIONAL pass the system won't auto-approve: routed to a human.
        # Demonstrates the review queue, the amber "Awaiting human review" gate in the
        # graph, and the Review panel. On this SEEDED row, Reject and Override (to a
        # real sign-on candidate) act against the DB; Approve is meaningful on a LIVE
        # review (where the proposed candidate is a real crew row).
        "trigger": "Sign-off initiated for Anders Holm (CM-1420)",
        "departing": {
            "crew_id": "CM-1420", "name": "Anders Holm", "rank": "Third Officer",
            "grade": "B", "vessel": "MV Baltic Trader", "port": "Hamburg", "nationality": "Norwegian",
        },
        "chosen": {
            "crew_id": "CM-2810", "name": "Marco Bianchi", "rank": "Third Officer",
            "grade": "B", "port": "Hamburg", "nationality": "Italian",
        },
        "confidence": 80.5,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Hamburg"],
        "alternatives": [
            {"crew_id": "CM-2844", "name": "Lars Eriksen", "rank": "Third Officer", "confidence_score": 74.0, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.805, "tokens_used": 0, "duration_ms": 3800},
            {"kind": "tool", "agent_name": "Compliance Agent", "tool_name": "validateDocuments", "input": '{"crew_id": "CM-2810"}', "output": '{"overall_status": "warning"}', "duration_ms": 140, "timestamp": None},
        ],
        "attempts": [
            {"order": 1, "crew_id": "CM-2810", "name": "Marco Bianchi", "rank": "Third Officer", "compliance_status": "warning", "compliance_score": 67.0, "failures": [], "warnings": ["Medical certificate expires in 21 days", "Flag-state endorsement pending renewal"]},
        ],
        "outcome_status": "pending",
        "compliance_status": "warning",
        "compliance_score": 67.0,
        "review_status": "pending_review",
        "review_trigger": "warning",
        "ai_proposal": {
            "crew_id": "CM-2810", "name": "Marco Bianchi", "rank": "Third Officer",
            "grade": "B", "port": "Hamburg", "nationality": "Italian",
            "compliance_status": "warning", "compliance_score": 67.0, "trigger": "warning",
        },
        "pending_reason": (
            "Marco Bianchi cleared compliance only conditionally (warning, 67%) — "
            "awaiting a human decision to approve the exception, reject, or pick another candidate."
        ),
        "total_tokens": 14300,
        "total_cost": 0.168,
        "cache_read_tokens": 8200,
        "cache_creation_tokens": 2600,
    },
]


decision_trace_service = DecisionTraceService()
