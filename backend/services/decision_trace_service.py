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
    insert_decision,
    list_decisions,
    update_outcome_by_workflow,
)
from database.models import WorkflowState

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

    async def record_outcome(
        self,
        workflow_id: str,
        *,
        outcome_status: str,
        compliance_status: Optional[str] = None,
        compliance_score: Optional[float] = None,
        outcome_reasons: Optional[list] = None,
        broadcast: Optional[Broadcast] = None,
    ) -> Optional[dict]:
        """Stamp the outcome (signed_on | rejected) on the workflow's decision. Never raises."""
        try:
            updated = await update_outcome_by_workflow(
                workflow_id,
                outcome_status=outcome_status,
                compliance_status=compliance_status,
                compliance_score=compliance_score,
                outcome_reasons=outcome_reasons,
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

    # ── Assembly ────────────────────────────────────────────────────────────────

    def _assemble(self, workflow: WorkflowState) -> dict:
        sign_off = workflow.sign_off_crew or {}
        matched = workflow.matched_crew or {}
        match_result = workflow.crew_match_result or {}
        chosen_id = workflow.matched_crew_id or matched.get("crew_id")

        # Alternatives = ranked candidates that were NOT chosen.
        ranked = match_result.get("ranked_candidates") or []
        alternatives = [c for c in ranked if c.get("crew_id") != chosen_id]

        reason = (workflow.memory or {}).get("short_term", {}).get("reason")

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

    async def seed_demo(self) -> dict:
        """Insert a couple of realistic mock decisions so the L4 view has data to show
        before any live workflow has run. Idempotent-ish: each call adds fresh rows."""
        seeded = []
        for spec in _DEMO_DECISIONS:
            record = self._mock_record(spec)
            seeded.append(await insert_decision(record))
        log.info("decision.seed_demo", count=len(seeded))
        return {"seeded": len(seeded), "decisions": seeded}

    def _mock_record(self, spec: Dict[str, Any]) -> dict:
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
            "outcome_status": spec["outcome_status"],
            "compliance_status": spec.get("compliance_status"),
            "compliance_score": spec.get("compliance_score"),
            "outcome_reasons": spec.get("outcome_reasons", []),
            "resolved_at": datetime.utcnow() if spec["outcome_status"] != "pending" else None,
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
        "outcome_reasons": ["Visa invalid for Rotterdam", "STCW certificate expired"],
        "total_tokens": 15200,
        "total_cost": 0.176,
        "cache_read_tokens": 9000,
        "cache_creation_tokens": 2800,
    },
    {
        "trigger": "Sign-off initiated for Liam O'Brien (CM-1190)",
        "departing": {
            "crew_id": "CM-1190", "name": "Liam O'Brien", "rank": "Master",
            "grade": "A", "vessel": "MV Northern Light", "port": "Houston", "nationality": "Irish",
        },
        "chosen": {
            "crew_id": "CM-2410", "name": "Sergey Volkov", "rank": "Master",
            "grade": "A", "port": "Houston", "nationality": "Russian",
        },
        "confidence": 88.1,
        "match_reasons": ["Exact rank match", "Grade matches", "Same port: Houston", "18 years experience"],
        "alternatives": [
            {"crew_id": "CM-2455", "name": "John Adams", "rank": "Master", "confidence_score": 83.2, "match_reasons": ["Exact rank match", "Grade matches"]},
            {"crew_id": "CM-2478", "name": "Yusuf Demir", "rank": "Master", "confidence_score": 76.0, "match_reasons": ["Exact rank match"]},
        ],
        "trajectory": [
            {"kind": "agent", "agent_name": "Crew Matching Agent", "agent_type": "crew_matching", "status": "completed", "confidence_score": 0.881, "tokens_used": 0, "duration_ms": 4500},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "searchCrew", "input": '{"rank": "Master", "port": "Houston"}', "output": '{"found": 3}', "duration_ms": 130, "timestamp": None},
            {"kind": "tool", "agent_name": "Crew Matching Agent", "tool_name": "rankCrew", "input": '{"candidates": ["CM-2410", "CM-2455", "CM-2478"]}', "output": '{"ranked_candidates": [{"crew_id": "CM-2410", "confidence_score": 88.1}]}', "duration_ms": 102, "timestamp": None},
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
]


decision_trace_service = DecisionTraceService()
