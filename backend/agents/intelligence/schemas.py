"""
L3 Intelligence-Graph data shapes (plain dataclasses → JSON via to_dict()).

Kept dependency-light and serializable so the supervisor can stream them over the
existing WebSocket vocabulary and the API can return them directly.
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SignOffContext:
    """The vacancy to fill, derived from the departing crew member."""
    vacated_rank: str
    vacated_grade: Optional[str] = None
    vessel: Optional[str] = None
    port: Optional[str] = None
    sign_off_date: Optional[str] = None
    contract_period_months: int = 6
    workflow_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Assessment:
    """One investigator's verdict on one candidate.

    score   — 0.0..1.0 contribution from this dimension.
    eligible— False means a HARD gate failed (disqualifies the candidate outright).
    signals — structured facts (for the UI / audit).
    reasons — short human-readable rationale fragments.
    """
    investigator: str
    crew_id: str
    score: float = 0.0
    eligible: bool = True
    signals: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InvestigatorReport:
    """Everything one investigator produces in a run: per-candidate assessments
    plus the rules/context it applied (surfaced to operators for transparency)."""
    investigator: str
    assessments: Dict[str, Assessment] = field(default_factory=dict)  # crew_id → Assessment
    applied: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investigator": self.investigator,
            "assessments": {k: v.to_dict() for k, v in self.assessments.items()},
            "applied": self.applied,
            "duration_ms": self.duration_ms,
        }


@dataclass
class RankedCandidate:
    """A fused, ranked placement recommendation."""
    rank_position: int
    crew_id: str
    name: str
    rank: str
    grade: Optional[str]
    nationality: Optional[str]
    port: Optional[str]
    score: float                       # fused 0..100
    rationale: List[str]               # combined top reasons across the 3 dimensions
    dimension_scores: Dict[str, float] = field(default_factory=dict)  # crew/contract/vessel 0..1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperatorNotification:
    """A notification dispatched to an operator or to the proposed crew (L3's
    'notify operators via the correct channel')."""
    recipient: str
    role: str
    channel: str
    status: str          # "delivered" | "failed" | "skipped"
    subject: str
    body: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IntelResult:
    """The supervisor's full output for one sign-off."""
    workflow_id: Optional[str]
    status: str                         # "matched" | "no_crew_found" | "error"
    context: Dict[str, Any]
    candidates: List[RankedCandidate] = field(default_factory=list)
    reports: List[InvestigatorReport] = field(default_factory=list)
    notifications: List[OperatorNotification] = field(default_factory=list)
    message: str = ""
    pool_size: int = 0
    disqualified: int = 0
    timing: Dict[str, int] = field(default_factory=dict)  # first_event_ms, total_ms
    # Derived node/edge graph for this run (vacancy → candidates → dimensions → L2
    # facts). Built by fit_graph.build_fit_graph; rendered live by the UI.
    fit_graph: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "context": self.context,
            "candidates": [c.to_dict() for c in self.candidates],
            "reports": [r.to_dict() for r in self.reports],
            "notifications": [n.to_dict() for n in self.notifications],
            "message": self.message,
            "pool_size": self.pool_size,
            "disqualified": self.disqualified,
            "timing": self.timing,
            "fit_graph": self.fit_graph,
        }
