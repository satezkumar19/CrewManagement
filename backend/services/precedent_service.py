"""
Precedent Service (L4) — the placement-history lookup.

Two responsibilities:

* **record_placement(decision)** — when a placement COMPLETES (its decision gets a
  signed_on / rejected outcome), append a flat precedent row to the history store.
* **consult(rank, grade, port)** — called at the START of a sign-off (a matching
  query). Looks up prior placements for the same vacancy profile and returns the
  matches + a summary, with `is_repeat=True` when ≥1 prior placement exists. This
  is what satisfies "Precedent Index consulted on the 2nd+ query."

Both are best-effort: failures are swallowed and logged so they can never break a
workflow turn (same convention as the decision-trace service).
"""
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

import structlog

from database.precedent_repository import find_precedents, insert_precedent

log = structlog.get_logger()

# broadcast(event_type, agent_name, data) -> Awaitable — same callback shape the
# WorkflowService uses to relay to the WebSocket manager.
Broadcast = Callable[[str, str, Dict[str, Any]], Awaitable[None]]


class PrecedentService:
    async def consult(
        self,
        rank: Optional[str],
        *,
        grade: Optional[str] = None,
        port: Optional[str] = None,
        nationality: Optional[str] = None,
        limit: int = 5,
        broadcast: Optional[Broadcast] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Look up prior placements for this vacancy profile. Never raises."""
        result: Dict[str, Any] = {
            "is_repeat": False,
            "matches": [],
            "summary": self._summarize([]),
            "query": {"rank": rank, "grade": grade, "port": port},
            "consulted_at": datetime.utcnow().isoformat(),
        }
        try:
            if rank:
                matches = await find_precedents(rank, port=port, grade=grade, limit=limit)
                result["matches"] = matches
                result["summary"] = self._summarize(matches)
                result["is_repeat"] = len(matches) > 0
            log.info(
                "precedent.consulted",
                rank=rank, port=port,
                matches=len(result["matches"]), is_repeat=result["is_repeat"],
            )
            if broadcast:
                await self._safe_broadcast(broadcast, "precedent_consulted", "Precedent Index", {
                    "workflow_id": workflow_id,
                    "rank": rank, "grade": grade, "port": port,
                    "is_repeat": result["is_repeat"],
                    "count": len(result["matches"]),
                    "summary": result["summary"],
                    "matches": result["matches"],
                    "message": (
                        f"Consulted Precedent Index for {rank} @ {port}: "
                        + (
                            f"{len(result['matches'])} prior placement(s) found"
                            if result["is_repeat"]
                            else "no precedent — first placement for this profile"
                        )
                    ),
                })
        except Exception:
            log.warning("precedent.consult.failed", rank=rank, port=port, exc_info=True)
        return result

    async def record_placement(self, decision: Dict[str, Any]) -> Optional[dict]:
        """Append a completed placement to the history store. Never raises.

        Only signed_on / rejected decisions are recorded — a 'pending' decision is
        not yet a completed placement.
        """
        try:
            status = decision.get("outcome_status")
            if status not in ("signed_on", "rejected"):
                return None
            dep = (decision.get("query_context") or {}).get("departing_crew") or {}
            chosen = decision.get("chosen_crew") or {}
            record = {
                "precedent_id": str(uuid4()),
                "decision_id": decision.get("decision_id"),
                "workflow_id": decision.get("workflow_id"),
                "created_at": datetime.utcnow(),
                "rank": dep.get("rank"),
                "grade": dep.get("grade"),
                "port": dep.get("port"),
                "nationality": dep.get("nationality"),
                "chosen_crew_id": chosen.get("crew_id"),
                "chosen_crew_name": chosen.get("name"),
                "chosen_crew_rank": chosen.get("rank"),
                # Chosen crew's own profile — the key the L4 #3 re-rank boosts on.
                "chosen_crew_nationality": chosen.get("nationality"),
                "chosen_crew_grade": chosen.get("grade"),
                "confidence_score": decision.get("confidence_score"),
                "outcome_status": status,
                "compliance_status": decision.get("compliance_status"),
                "compliance_score": decision.get("compliance_score"),
                # L4 HITL — carry how the placement was decided so consult() can
                # weight human-reviewed precedents above AI auto-decisions.
                "decision_source": decision.get("decision_source") or "ai",
                "reviewed_by": decision.get("reviewed_by"),
                "review_reason": decision.get("review_reason"),
            }
            stored = await insert_precedent(record)
            log.info(
                "precedent.recorded",
                rank=record["rank"], port=record["port"], outcome=status,
                source=record["decision_source"],
            )
            return stored
        except Exception:
            log.warning("precedent.record.failed", exc_info=True)
            return None

    # ── Feedback into L3 (#3) ───────────────────────────────────────────────────

    def derive_guidance(self, precedent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Turn a consult() result into a compact scorer-facing profile (L4 #3).

        For each prior placement of this vacancy profile: a signed_on outcome makes
        its chosen crew's nationality/grade PREFERRED (weighted by how cleanly it
        cleared compliance); a rejected outcome makes that nationality AVOIDED. The
        Crew Matching scorer reads this to boost/penalize candidates accordingly.

        Weights are normalized to [0, 1] — the scorer maps them onto a point boost.
        Returns has_precedent=False (everything empty) when there's nothing to learn
        from, so first-time vacancies are a no-op. Never raises.
        """
        guidance: Dict[str, Any] = {
            "has_precedent": False,
            "prefer_nationalities": {},
            "avoid_nationalities": {},
            "prefer_grades": {},
            "rationale": None,
            "summary": (precedent or {}).get("summary"),
            # L4 HITL — True when at least one consulted precedent was human-reviewed.
            "human_precedent": False,
        }
        try:
            matches = (precedent or {}).get("matches") or []
            if not matches:
                return guidance
            # A human-reviewed precedent is a stronger signal than an AI auto-decision,
            # so its prefer/avoid weight is boosted (capped at 1.0).
            HUMAN_BOOST = 1.25
            prefer_nat: Dict[str, float] = {}
            avoid_nat: Dict[str, float] = {}
            prefer_grade: Dict[str, float] = {}
            signed_examples: List[str] = []
            human_seen = False
            for m in matches:
                nat = m.get("chosen_crew_nationality")
                grade = m.get("chosen_crew_grade")
                outcome = m.get("outcome_status")
                score = m.get("compliance_score")
                human = bool(m.get("human_reviewed"))
                human_seen = human_seen or human
                boost = HUMAN_BOOST if human else 1.0
                if outcome == "signed_on":
                    w = round(min(1.0, ((score if score is not None else 85.0) / 100.0) * boost), 3)
                    if nat:
                        prefer_nat[nat] = max(prefer_nat.get(nat, 0.0), w)
                    if grade:
                        prefer_grade[grade] = max(prefer_grade.get(grade, 0.0), w)
                    name = m.get("chosen_crew_name") or m.get("chosen_crew_id") or "prior crew"
                    detail = f"{name}"
                    if nat:
                        detail += f" ({nat}"
                        detail += f", {grade})" if grade else ")"
                    if score is not None:
                        detail += f" cleared at {score:.0f}%"
                    if human:
                        reviewer = m.get("reviewed_by")
                        detail += f" — human-approved{f' by {reviewer}' if reviewer else ''}"
                    signed_examples.append(detail)
                elif outcome == "rejected":
                    # Penalty scaled by how badly it failed (low compliance → stronger
                    # avoid); a human rejection weighs more heavily still.
                    base = 1.0 - (score if score is not None else 40.0) / 100.0
                    w = round(min(1.0, max(base, 0.2) * boost), 3)
                    if nat:
                        avoid_nat[nat] = max(avoid_nat.get(nat, 0.0), w)
            # A nationality that both signed on AND was rejected before: prefer wins
            # (a confirmed success outweighs a single failure for the same profile).
            for nat in list(avoid_nat):
                if nat in prefer_nat:
                    avoid_nat.pop(nat, None)
            has = bool(prefer_nat or avoid_nat or prefer_grade)
            rationale = (
                "Prior signed-on: " + "; ".join(signed_examples)
                if signed_examples
                else ("Prior placements for this profile were rejected" if avoid_nat else None)
            )
            if rationale and human_seen:
                rationale += " · weighted up: includes human-reviewed precedent(s)"
            guidance.update({
                "has_precedent": has,
                "prefer_nationalities": prefer_nat,
                "avoid_nationalities": avoid_nat,
                "prefer_grades": prefer_grade,
                "rationale": rationale,
                "human_precedent": human_seen,
            })
        except Exception:
            log.warning("precedent.derive_guidance.failed", exc_info=True)
        return guidance

    # ── helpers ───────────────────────────────────────────────────────────────

    def _summarize(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(matches)
        signed = sum(1 for m in matches if m.get("outcome_status") == "signed_on")
        rejected = sum(1 for m in matches if m.get("outcome_status") == "rejected")
        human_reviewed = sum(1 for m in matches if m.get("human_reviewed"))
        scores = [m["compliance_score"] for m in matches if m.get("compliance_score") is not None]
        avg = round(sum(scores) / len(scores), 1) if scores else None
        last = matches[0] if matches else None  # most recent (rows are desc by created_at)
        return {
            "total": total,
            "signed_on": signed,
            "rejected": rejected,
            # L4 HITL — how many of these prior placements were human-reviewed.
            "human_reviewed": human_reviewed,
            "avg_compliance_score": avg,
            "last_choice": (
                {
                    "name": last.get("chosen_crew_name"),
                    "outcome": last.get("outcome_status"),
                    "human_reviewed": bool(last.get("human_reviewed")),
                }
                if last else None
            ),
        }

    async def _safe_broadcast(
        self, broadcast: Broadcast, event_type: str, agent_name: str, data: Dict[str, Any]
    ) -> None:
        try:
            await broadcast(event_type, agent_name, data)
        except Exception:
            pass


precedent_service = PrecedentService()
