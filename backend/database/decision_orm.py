"""
SQLAlchemy ORM model for L4 Decision Traces.

A Decision Trace is the formalized, persisted record of ONE crew-placement
decision made by the L3 intelligence layer (Master Agent + specialists). L4 does
not re-decide anything — it CAPTURES what L3 produced (the query context, the
chosen crew, the alternatives weighed, the full agent trajectory) into one
queryable row, then later stamps the OUTCOME (signed-on / rejected) once the
compliance gate resolves.

This single table is the seed every later L4 phase reads from: the Precedent
Index (query by rank/port), Pattern Detection (aggregate over rows), and the
feedback loop back into L3 (read precedent before ranking).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String

from database.db import Base


class DecisionTrace(Base):
    __tablename__ = "decision_traces"

    decision_id = Column(String, primary_key=True)          # uuid
    workflow_id = Column(String, index=True, nullable=False)  # the L3 run that produced it
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)           # when the outcome was stamped

    # ── The question (from sign_off_crew + trigger) ────────────────────────────
    trigger = Column(String, nullable=True)
    query_context = Column(JSON, nullable=True)             # departing crew profile + reason

    # ── The decision (from matched_crew / crew_match_result) ───────────────────
    chosen_crew_id = Column(String, nullable=True)
    chosen_crew = Column(JSON, nullable=True)               # {name, rank, grade, port, nationality}
    confidence_score = Column(Float, nullable=True)
    match_reasons = Column(JSON, nullable=True)             # List[str] — the "why"
    alternatives = Column(JSON, nullable=True)              # ranked candidates NOT chosen

    # ── The trajectory (flattened from agent_executions[]) ─────────────────────
    trajectory = Column(JSON, nullable=True)                # ordered [{agent, tool, input, output, ...}]

    # ── Precedent Index (L4 #2) — what the history lookup returned at the start ─
    # of this matching query. is_repeat_query is True when ≥1 prior placement for
    # the same vacancy profile existed (i.e. this is the 2nd+ query for it).
    is_repeat_query = Column(Boolean, default=False)
    consulted_precedents = Column(JSON, nullable=True)      # {is_repeat, matches, summary, query}

    # ── Precedent feedback into L3 (L4 #3) — how the consulted precedent re-ranked
    # the match. NULL/applied=False for first-time vacancies (no precedent boost).
    precedent_feedback = Column(JSON, nullable=True)        # {applied, lift, reranked, boosted, rationale}

    # ── The outcome (NULL at capture; filled at the compliance gate) ───────────
    outcome_status = Column(String, default="pending")      # pending | signed_on | rejected
    compliance_status = Column(String, nullable=True)       # passed | warning | failed
    compliance_score = Column(Float, nullable=True)
    outcome_reasons = Column(JSON, nullable=True)           # warnings (conditional) or failures
    # ── Rejection-retry loop (L4 #4) — each candidate Compliance was run against,
    # in order, until one passed (or the alternatives were exhausted). pending_reason
    # explains a still-pending decision; cleared once the outcome is stamped.
    attempts = Column(JSON, nullable=True)                  # [{order, crew_id, name, compliance_status, ...}]
    pending_reason = Column(String, nullable=True)

    # ── Human-in-the-loop review (L4 HITL) ─────────────────────────────────────
    # How the decision was resolved, and (if a human was involved) their verdict.
    # decision_source: 'ai' (fully automated) | 'human' (a reviewer approved/rejected
    # a case the AI couldn't finish) | 'ai_then_human' (a human OVERRODE the AI's pick).
    # review_status: NULL (never needed review) | 'pending_review' | 'approved' |
    # 'rejected' | 'overridden'. review_trigger records WHY review was asked
    # ('warning' | 'exhausted'). ai_proposal freezes what the AI wanted before any
    # human override, so the audit shows what changed.
    decision_source = Column(String, default="ai")
    review_status = Column(String, nullable=True)
    review_trigger = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_reason = Column(String, nullable=True)           # reason code/category
    review_comments = Column(String, nullable=True)         # free-text rationale
    review_evidence = Column(JSON, nullable=True)           # [{type, label, ref}]
    ai_proposal = Column(JSON, nullable=True)               # frozen AI decision pre-override

    # ── Decision metadata (cost of reaching it) ────────────────────────────────
    session_id = Column(String, nullable=True)
    total_tokens = Column(Integer, default=0)
    total_cost = Column(Float, default=0.0)
    cache_read_tokens = Column(Integer, default=0)
    cache_creation_tokens = Column(Integer, default=0)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "trigger": self.trigger,
            "query_context": self.query_context or {},
            "chosen_crew_id": self.chosen_crew_id,
            "chosen_crew": self.chosen_crew or {},
            "confidence_score": self.confidence_score,
            "match_reasons": self.match_reasons or [],
            "alternatives": self.alternatives or [],
            "trajectory": self.trajectory or [],
            "is_repeat_query": bool(self.is_repeat_query),
            "consulted_precedents": self.consulted_precedents,
            "precedent_feedback": self.precedent_feedback,
            "outcome_status": self.outcome_status,
            "compliance_status": self.compliance_status,
            "compliance_score": self.compliance_score,
            "outcome_reasons": self.outcome_reasons or [],
            "attempts": self.attempts or [],
            "pending_reason": self.pending_reason,
            "decision_source": self.decision_source or "ai",
            "review_status": self.review_status,
            "review_trigger": self.review_trigger,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_reason": self.review_reason,
            "review_comments": self.review_comments,
            "review_evidence": self.review_evidence or [],
            "ai_proposal": self.ai_proposal,
            "session_id": self.session_id,
            "total_tokens": self.total_tokens or 0,
            "total_cost": round(self.total_cost or 0.0, 6),
            "cache_read_tokens": self.cache_read_tokens or 0,
            "cache_creation_tokens": self.cache_creation_tokens or 0,
        }
