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

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String

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

    # ── The outcome (NULL at capture; filled at the compliance gate) ───────────
    outcome_status = Column(String, default="pending")      # pending | signed_on | rejected
    compliance_status = Column(String, nullable=True)       # passed | warning | failed
    compliance_score = Column(Float, nullable=True)
    outcome_reasons = Column(JSON, nullable=True)           # warnings (conditional) or failures

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
            "outcome_status": self.outcome_status,
            "compliance_status": self.compliance_status,
            "compliance_score": self.compliance_score,
            "outcome_reasons": self.outcome_reasons or [],
            "session_id": self.session_id,
            "total_tokens": self.total_tokens or 0,
            "total_cost": round(self.total_cost or 0.0, 6),
            "cache_read_tokens": self.cache_read_tokens or 0,
            "cache_creation_tokens": self.cache_creation_tokens or 0,
        }
