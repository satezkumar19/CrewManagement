"""
BaseInvestigator — shared contract for the three L3 specialist investigators.

An investigator is a focused, read-only analyst: given the sign-off context and the
candidate pool, it returns one `Assessment` per candidate from its own dimension
(crew eligibility / contract & wage / vessel ops). It deliberately does NOT rank or
decide across dimensions — that is the Supervisor's job (see ranking.py). This keeps
each investigator independently testable and swappable (today: Python over fallback
rule data; later: a Managed-Agents sub-agent querying L2's graph).
"""
import time
from typing import Any, Callable, Dict, List, Optional

from agents.intelligence.schemas import Assessment, InvestigatorReport, SignOffContext


class BaseInvestigator:
    #: stable key used in dimension_scores / fusion weights and event labels
    key: str = "base"
    #: human-facing name
    name: str = "Investigator"

    def __init__(self, event_callback: Optional[Callable] = None):
        self.event_callback = event_callback

    async def investigate(
        self, context: SignOffContext, candidates: List[Dict[str, Any]]
    ) -> InvestigatorReport:
        """Run the investigator over the pool, timing and event-wrapping the work."""
        t0 = time.perf_counter()
        await self._emit("intel_investigator_started", {
            "investigator": self.name, "key": self.key, "pool_size": len(candidates),
        })

        # Per-run async pre-fetch shared across all candidates (e.g. L2 graph reads
        # that are per-port, not per-candidate). Investigators that don't need it
        # inherit the no-op default.
        prep = await self._prepare(context)

        assessments: Dict[str, Assessment] = {}
        applied: Dict[str, Any] = {}
        for crew in candidates:
            assessments[crew["crew_id"]] = self._assess(context, crew, applied, prep)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        report = InvestigatorReport(
            investigator=self.name, assessments=assessments, applied=applied, duration_ms=duration_ms
        )
        eligible = sum(1 for a in assessments.values() if a.eligible)
        await self._emit("intel_investigator_completed", {
            "investigator": self.name, "key": self.key,
            "eligible": eligible, "assessed": len(assessments), "duration_ms": duration_ms,
        })
        return report

    async def _prepare(self, context: SignOffContext) -> Dict[str, Any]:
        """Optional async pre-fetch run ONCE per investigate() before the candidate
        loop — the place to read L2 graph facts that are per-port, not per-candidate.
        Default: nothing. The returned dict is passed to every _assess() call."""
        return {}

    def _assess(
        self, context: SignOffContext, crew: Dict[str, Any], applied: Dict[str, Any], prep: Dict[str, Any]
    ) -> Assessment:
        """Subclasses implement: produce one Assessment for one candidate.
        `applied` is a shared dict the investigator may populate with the rules/
        context it used (returned to operators for transparency). `prep` carries any
        per-run data from _prepare() (e.g. L2 graph lookups)."""
        raise NotImplementedError

    async def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if not self.event_callback:
            return
        try:
            await self.event_callback(event_type=event_type, agent_name=self.name, data=data)
        except Exception:
            pass

    # ── small scoring helpers shared by investigators ──────────────────────────────
    @staticmethod
    def _clamp(x: float) -> float:
        return max(0.0, min(1.0, x))
