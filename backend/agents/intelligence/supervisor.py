"""
L3 Intelligence-Graph Supervisor.

Implements the image's "Supervisor + specialist investigators" pattern:

    sign-off context ─▶ Supervisor
                         ├─ Crew Intel          (availability, certs, rank eligibility)
                         ├─ Contract/Wage Intel (applicable rules for the period)   [run in parallel]
                         └─ Vessel Ops Intel     (requirements + port schedule)
                         ▼
                     fuse → top-3 ranked candidates (with rationale)
                         ▼
                     notify operators via the correct channel

Events are streamed through the SAME callback/WebSocket vocabulary the rest of the
app uses (`intel_*` event types), so the existing streaming layer renders L3 with
no change. The orchestration is deterministic Python over fallback rule data — it
runs with no API key and no graph infra (the repo's "fallback" philosophy), and the
investigator seam can later be backed by Managed-Agents sub-agents querying L2.

Exit criteria covered here: delegates to all 3 investigators · returns top-3 with
rationale · notifies via the correct channel · handles "no crew found" gracefully ·
records first-token / total latency for the <2s / <10s SLOs.
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from agents.intelligence.contract_wage_intel import ContractWageIntel
from agents.intelligence.crew_intel import CrewIntel
from agents.intelligence.fit_graph import build_fit_graph
from agents.intelligence.graph_gateway import backend as l2_backend
from agents.intelligence.notifications import OperatorNotifier
from agents.intelligence.ranking import fuse
from agents.intelligence.schemas import IntelResult, SignOffContext
from agents.intelligence.vessel_ops_intel import VesselOpsIntel

log = structlog.get_logger()

CrewProvider = Callable[[], Awaitable[List[Dict[str, Any]]]]


class IntelligenceSupervisor:
    name = "Intelligence Supervisor"

    def __init__(
        self,
        event_callback: Optional[Callable] = None,
        crew_provider: Optional[CrewProvider] = None,
        notifier: Optional[OperatorNotifier] = None,
    ):
        self.event_callback = event_callback
        # Default pool = the Postgres sign-on pool; injectable for tests / future graph reads.
        self._crew_provider = crew_provider
        self.investigators = [
            CrewIntel(event_callback),
            ContractWageIntel(event_callback),
            VesselOpsIntel(event_callback),
        ]
        self.notifier = notifier or OperatorNotifier(event_callback)

    async def find_replacements(
        self, context: SignOffContext, top_n: int = 3
    ) -> IntelResult:
        t0 = time.perf_counter()
        first_event_ms = {"v": None}

        async def emit(event_type: str, data: Dict[str, Any]) -> None:
            if first_event_ms["v"] is None:
                first_event_ms["v"] = int((time.perf_counter() - t0) * 1000)
            await self._emit(event_type, data)

        await emit("intel_supervisor_started", {
            "workflow_id": context.workflow_id,
            "vacated_rank": context.vacated_rank,
            "vessel": context.vessel,
            "port": context.port,
            "investigators": [i.name for i in self.investigators],
        })

        # ── Load candidate pool ───────────────────────────────────────────────────
        candidates = await self._load_candidates()
        candidates_by_id = {c["crew_id"]: c for c in candidates}

        # ── Delegate to all 3 investigators IN PARALLEL ───────────────────────────
        reports = await asyncio.gather(
            *(inv.investigate(context, candidates) for inv in self.investigators)
        )

        # ── Fuse → top-N ranked candidates with rationale ─────────────────────────
        ranked = fuse(list(reports), candidates_by_id, top_n=top_n)
        disqualified = len(candidates) - sum(
            1 for cid in candidates_by_id
            if all(
                (r.assessments.get(cid).eligible if r.assessments.get(cid) else False)
                for r in reports
            )
        )

        result = IntelResult(
            workflow_id=context.workflow_id,
            status="matched" if ranked else "no_crew_found",
            context=context.to_dict(),
            candidates=ranked,
            reports=list(reports),
            pool_size=len(candidates),
            disqualified=disqualified,
        )

        if ranked:
            await emit("intel_ranking", {
                "workflow_id": context.workflow_id,
                "top_n": len(ranked),
                "candidates": [c.to_dict() for c in ranked],
            })
            result.message = (
                f"Top {len(ranked)} candidate(s) for {context.vacated_rank} at {context.port}: "
                + ", ".join(f"#{c.rank_position} {c.name} ({c.score})" for c in ranked)
            )
        else:
            # ── Graceful "no crew found" ──────────────────────────────────────────
            result.message = (
                f"No eligible crew for {context.vacated_rank} at {context.port}: "
                f"0 of {len(candidates)} candidates cleared availability/rank/cert gates."
            )
            await emit("intel_no_crew", {
                "workflow_id": context.workflow_id,
                "pool_size": len(candidates),
                "message": result.message,
            })
            log.info("intel.no_crew", workflow_id=context.workflow_id, pool=len(candidates))

        # ── Derive the L3 fit graph (vacancy → candidates → dimensions → L2) ──────
        result.fit_graph = build_fit_graph(
            context, candidates_by_id, list(reports), ranked, backend=l2_backend()
        )
        await emit("intel_graph", {
            "workflow_id": context.workflow_id,
            "nodes": result.fit_graph["nodes"],
            "edges": result.fit_graph["edges"],
            "backend": result.fit_graph["backend"],
            "node_count": result.fit_graph["node_count"],
            "edge_count": result.fit_graph["edge_count"],
        })

        # ── Notify operators via the correct channel (always — match OR no-crew) ──
        result.notifications = await self.notifier.notify(result, context)

        total_ms = int((time.perf_counter() - t0) * 1000)
        result.timing = {"first_event_ms": first_event_ms["v"] or 0, "total_ms": total_ms}
        await emit("intel_supervisor_completed", {
            "workflow_id": context.workflow_id,
            "status": result.status,
            "shortlisted": len(ranked),
            "notifications": len(result.notifications),
            "timing": result.timing,
        })
        return result

    # ── helpers ──────────────────────────────────────────────────────────────────
    async def _load_candidates(self) -> List[Dict[str, Any]]:
        if self._crew_provider is not None:
            return await self._crew_provider()
        # Lazy import so the module loads without the DB stack for unit tests.
        from database.crew_repository import get_sign_on_crew
        return await get_sign_on_crew()

    async def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if not self.event_callback:
            return
        try:
            await self.event_callback(event_type=event_type, agent_name=self.name, data=data)
        except Exception:
            pass


def context_from_signoff_crew(
    crew: Dict[str, Any], contract_period_months: int = 6, workflow_id: Optional[str] = None
) -> SignOffContext:
    """Build the L3 vacancy context from a departing crew member's record."""
    return SignOffContext(
        vacated_rank=crew.get("rank", ""),
        vacated_grade=crew.get("grade"),
        vessel=crew.get("vessel"),
        port=crew.get("port"),
        sign_off_date=crew.get("sign_off_date"),
        contract_period_months=contract_period_months,
        workflow_id=workflow_id,
    )
