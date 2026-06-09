"""
SQLAlchemy ORM model for the L4 decision audit trail (HITL).

Append-only: one immutable row per state transition of a decision — the AI's
automated outcome, a `review_requested` pause, and each human approve / reject /
override. `decision_traces` holds the CURRENT state of a decision; this table
holds its full HISTORY, so the complete chain of who-did-what-when survives even
after a human overrides the AI's pick.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String

from database.db import Base


class DecisionAudit(Base):
    __tablename__ = "decision_audit"

    audit_id = Column(String, primary_key=True)             # uuid
    decision_id = Column(String, index=True, nullable=False)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String, nullable=True)                   # 'system' | reviewer name/id
    # 'review_requested' | 'review_approve' | 'review_reject' | 'review_override'
    action = Column(String, nullable=False)
    from_state = Column(String, nullable=True)              # prior outcome/review state
    to_state = Column(String, nullable=True)                # resulting outcome/review state
    reason = Column(String, nullable=True)                  # trigger or reviewer reason code
    comments = Column(String, nullable=True)                # free-text rationale
    evidence = Column(JSON, nullable=True)                  # [{type, label, ref}]

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "decision_id": self.decision_id,
            "ts": self.ts.isoformat() if self.ts else None,
            "actor": self.actor,
            "action": self.action,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
            "comments": self.comments,
            "evidence": self.evidence or [],
        }
