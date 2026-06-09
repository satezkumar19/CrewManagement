"""
Managed-Agents L3 Supervisor.

The LLM-backed counterpart to `supervisor.IntelligenceSupervisor`: instead of calling
the three investigators directly in-process, it opens a **Claude Managed-Agents
coordinator session** and lets the hosted coordinator natively delegate to the three
specialist sub-agents (see `managed_registry.py`). The sub-agents' tool calls are
resolved back here by `IntelToolRouter`, which runs the *existing* deterministic
investigators — so the per-candidate scores/gates feeding fusion are identical to the
fallback path, and ranking/notification stay deterministic.

Same `find_replacements(context, top_n) -> IntelResult` contract as the deterministic
supervisor, so the API and the whole frontend are unchanged. Selected via
`INTEL_BACKEND=managed` (see config + `api/routes/intelligence.py`).

Graceful degradation: if the hosted turn errors or times out, `router.reports()` still
backfills every dimension deterministically, so a valid ranked result is always returned.
"""
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from agents.intelligence.fit_graph import build_fit_graph
from agents.intelligence.graph_gateway import backend as l2_backend
from agents.intelligence.managed_registry import (
    IntelToolRouter,
    intelligence_coordinator_config,  # noqa: F401  (kept for discoverability/setup parity)
)
from agents.intelligence.notifications import OperatorNotifier
from agents.intelligence.ranking import fuse
from agents.intelligence.schemas import IntelResult, SignOffContext
from agents.managed.client import ManagedAgentsClient
from config import settings

log = structlog.get_logger()

CrewProvider = Callable[[], Awaitable[List[Dict[str, Any]]]]

# Stable (key, display name) for the three investigators — names map cleanly onto the
# frontend's investigator keys (crew / contract|wage / vessel).
_INVESTIGATORS = [("crew", "Crew Intel"), ("contract", "Contract/Wage Intel"), ("vessel", "Vessel Ops Intel")]


class ManagedIntelligenceSupervisor:
    name = "Intelligence Supervisor"
    backend = "managed"

    def __init__(
        self,
        event_callback: Optional[Callable] = None,
        crew_provider: Optional[CrewProvider] = None,
        notifier: Optional[OperatorNotifier] = None,
    ):
        self.event_callback = event_callback
        self._crew_provider = crew_provider
        self.notifier = notifier or OperatorNotifier(event_callback)

    async def find_replacements(self, context: SignOffContext, top_n: int = 3) -> IntelResult:
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
            "backend": self.backend,
            "investigators": [name for _k, name in _INVESTIGATORS],
        })

        # ── Load pool + build the tool router (holds the existing investigators) ──
        candidates = await self._load_candidates()
        candidates_by_id = {c["crew_id"]: c for c in candidates}
        router = IntelToolRouter(context, candidates)

        # The three investigators run concurrently as hosted sub-agents.
        for key, inv_name in _INVESTIGATORS:
            await emit("intel_investigator_started", {
                "investigator": inv_name, "key": key, "pool_size": len(candidates),
            })

        # ── Drive ONE coordinator turn (hosted delegation) ───────────────────────
        await self._run_coordinator_turn(context, candidates, router, emit)

        # ── Authoritative reports (backfills any dimension the LLM skipped) ───────
        reports = await router.reports()
        for report in reports:
            eligible = sum(1 for a in report.assessments.values() if a.eligible)
            await emit("intel_investigator_completed", {
                "investigator": report.investigator,
                "key": _key_for(report.investigator),
                "eligible": eligible,
                "assessed": len(report.assessments),
                "duration_ms": report.duration_ms,
            })

        # ── Fuse → top-N (deterministic), then graph + notify (shared logic) ──────
        ranked = fuse(reports, candidates_by_id, top_n=top_n)
        disqualified = len(candidates) - sum(
            1 for cid in candidates_by_id
            if all((r.assessments.get(cid).eligible if r.assessments.get(cid) else False) for r in reports)
        )

        result = IntelResult(
            workflow_id=context.workflow_id,
            status="matched" if ranked else "no_crew_found",
            context=context.to_dict(),
            candidates=ranked,
            reports=reports,
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
                f"[managed] Top {len(ranked)} candidate(s) for {context.vacated_rank} at {context.port}: "
                + ", ".join(f"#{c.rank_position} {c.name} ({c.score})" for c in ranked)
            )
        else:
            result.message = (
                f"[managed] No eligible crew for {context.vacated_rank} at {context.port}: "
                f"0 of {len(candidates)} candidates cleared availability/rank/cert gates."
            )
            await emit("intel_no_crew", {
                "workflow_id": context.workflow_id,
                "pool_size": len(candidates),
                "message": result.message,
            })

        result.fit_graph = build_fit_graph(
            context, candidates_by_id, reports, ranked, backend=l2_backend()
        )
        await emit("intel_graph", {
            "workflow_id": context.workflow_id,
            "nodes": result.fit_graph["nodes"],
            "edges": result.fit_graph["edges"],
            "backend": result.fit_graph["backend"],
            "node_count": result.fit_graph["node_count"],
            "edge_count": result.fit_graph["edge_count"],
        })

        result.notifications = await self.notifier.notify(result, context)

        total_ms = int((time.perf_counter() - t0) * 1000)
        result.timing = {"first_event_ms": first_event_ms["v"] or 0, "total_ms": total_ms}
        await emit("intel_supervisor_completed", {
            "workflow_id": context.workflow_id,
            "status": result.status,
            "backend": self.backend,
            "shortlisted": len(ranked),
            "notifications": len(result.notifications),
            "timing": result.timing,
        })
        return result

    # ── helpers ──────────────────────────────────────────────────────────────────
    async def _run_coordinator_turn(
        self, context: SignOffContext, candidates: List[Dict[str, Any]],
        router: IntelToolRouter, emit: Callable,
    ) -> None:
        """Open the coordinator session and drive one turn. Best-effort: any failure is
        logged and swallowed — `router.reports()` then backfills deterministically."""
        coord_id = settings.managed_l3_coordinator_agent_id
        env_id = settings.managed_l3_environment_id
        if not coord_id or not env_id:
            log.warning("intel.managed.not_configured", coord=bool(coord_id), env=bool(env_id))
            return

        async def relay(etype: str, payload: Dict[str, Any]) -> None:
            # Surface the coordinator/sub-agent narrative into the live trace.
            if etype == "agent.message" and payload.get("text"):
                await self._emit("intel_agent_message", {
                    "workflow_id": context.workflow_id,
                    "agent": payload.get("agent_name") or "Intelligence Supervisor",
                    "text": payload.get("text", "")[:500],
                })

        try:
            client = ManagedAgentsClient()
            session = await client.client.beta.sessions.create(
                agent=coord_id, environment_id=env_id,
                title=f"L3 Intelligence · {context.vacated_rank} @ {context.port}",
            )
            turn = await client.run_turn(
                session.id, _kickoff_message(context, candidates), router, on_event=relay
            )
            log.info(
                "intel.managed.turn_done",
                triggered=router.triggered_keys(),
                usage=turn.get("usage"),
            )
        except Exception:
            log.warning("intel.managed.turn_failed", exc_info=True)

    async def _load_candidates(self) -> List[Dict[str, Any]]:
        if self._crew_provider is not None:
            return await self._crew_provider()
        from database.crew_repository import get_sign_on_crew
        return await get_sign_on_crew()

    async def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if not self.event_callback:
            return
        try:
            await self.event_callback(event_type=event_type, agent_name=self.name, data=data)
        except Exception:
            pass


async def stream_managed_narration(
    context: SignOffContext,
    event_callback: Callable,
    crew_provider: Optional[CrewProvider] = None,
) -> None:
    """Fast-path enrichment (SLO-safe): run the Managed-Agents coordinator + 3 sub-agents
    purely to STREAM their reasoning, *after* the deterministic result has already been
    returned to the caller. Emits `intel_narration_started`, `intel_agent_message` (per
    coordinator/sub-agent message), and `intel_narration_done`. It deliberately does NOT
    rank/graph/notify — the deterministic path already did, authoritatively — so the
    <10s response SLO is met while the real agents enrich the UI behind it.

    Best-effort: any failure is logged and swallowed (the user already has their result).
    """
    coord_id = settings.managed_l3_coordinator_agent_id
    env_id = settings.managed_l3_environment_id
    if not coord_id or not env_id:
        return

    async def emit(event_type: str, data: Dict[str, Any]) -> None:
        try:
            await event_callback(event_type=event_type, agent_name="Intelligence Supervisor", data=data)
        except Exception:
            pass

    if crew_provider is not None:
        candidates = await crew_provider()
    else:
        from database.crew_repository import get_sign_on_crew
        candidates = await get_sign_on_crew()
    router = IntelToolRouter(context, candidates)

    await emit("intel_narration_started", {
        "workflow_id": context.workflow_id,
        "investigators": [name for _k, name in _INVESTIGATORS],
    })

    async def relay(etype: str, payload: Dict[str, Any]) -> None:
        if etype == "agent.message" and (payload.get("text") or "").strip():
            await emit("intel_agent_message", {
                "workflow_id": context.workflow_id,
                "agent": payload.get("agent_name") or "Intelligence Supervisor",
                "text": (payload.get("text") or "").strip()[:600],
            })

    try:
        client = ManagedAgentsClient()
        session = await client.client.beta.sessions.create(
            agent=coord_id, environment_id=env_id,
            title=f"L3 narration · {context.vacated_rank} @ {context.port}",
        )
        await client.run_turn(
            session.id, _kickoff_message(context, candidates), router, on_event=relay
        )
        log.info("intel.narration_done", triggered=router.triggered_keys())
    except Exception:
        log.warning("intel.narration_failed", exc_info=True)
    finally:
        await emit("intel_narration_done", {"workflow_id": context.workflow_id})


def _kickoff_message(context: SignOffContext, candidates: List[Dict[str, Any]]) -> str:
    grade = f" ({context.vacated_grade})" if context.vacated_grade else ""
    return (
        f"VACANCY: {context.vacated_rank}{grade} on {context.vessel or 'a vessel'} "
        f"at {context.port or 'the join port'}; {context.contract_period_months}-month contract.\n"
        f"CANDIDATE POOL: {len(candidates)} sign-on crew, already loaded into your "
        f"investigators' assess tools (call them with no arguments).\n"
        "Delegate to ALL THREE investigators in parallel; each must call its assess tool "
        "to evaluate every candidate, then report its findings. Do NOT rank — the platform "
        "fuses their scores deterministically."
    )


def _key_for(investigator_name: str) -> str:
    n = (investigator_name or "").lower()
    if "crew" in n:
        return "crew"
    if "contract" in n or "wage" in n:
        return "contract"
    if "vessel" in n:
        return "vessel"
    return n
