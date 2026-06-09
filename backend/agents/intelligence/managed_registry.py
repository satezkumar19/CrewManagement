"""
Managed-Agents wiring for the L3 Intelligence Graph.

Turns the deterministic Supervisor + 3 investigators into a real **Claude
Managed-Agents** topology: an LLM **coordinator** (the Supervisor) that natively
delegates to three LLM **specialist sub-agents** (Crew Intel / Contract-Wage Intel /
Vessel Ops Intel). Each sub-agent reasons and narrates, but the authoritative numeric
score + hard gates come from a **deterministic tool** that simply runs the existing
investigator (`crew_intel` / `contract_wage_intel` / `vessel_ops_intel`) — so ranking
stays deterministic and the acceptance-criteria scenarios remain valid.

Two responsibilities (mirrors `agents/managed/registry.py`):

1. **Setup-time config** — `intelligence_specialist_configs()` /
   `intelligence_coordinator_config()`: the persisted-agent definitions handed to
   `client.beta.agents.create(...)` by `scripts/setup_l3_agents.py`.
2. **Runtime routing** — `IntelToolRouter`: resolves the `agent.custom_tool_use` events
   the hosted sub-agents emit, by delegating to the existing investigators, and caches
   one `InvestigatorReport` per dimension for the (deterministic) fusion step.
"""
from typing import Any, Dict, List, Optional

import structlog

from agents.intelligence.contract_wage_intel import ContractWageIntel
from agents.intelligence.crew_intel import CrewIntel
from agents.intelligence.graph_gateway import port_restriction_facts
from agents.intelligence.schemas import InvestigatorReport, SignOffContext
from agents.intelligence.vessel_ops_intel import VesselOpsIntel
from config import settings
from database.intel_rules import (
    DECK_LADDER,
    ENGINE_LADDER,
    RATING_GROUP,
    STANDARD_CONTRACT,
    departure_window_days,
    join_by_date,
    rank_family,
    wage_band,
)

log = structlog.get_logger()

COORDINATOR_NAME = "Intelligence Supervisor"

# Tool name → investigator key (names are globally unique, like the other flow).
_ASSESS_TOOL_TO_KEY = {
    "assess_crew": "crew",
    "assess_contract": "contract",
    "assess_vessel": "vessel",
}

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


# ── Persisted-agent configs (one-time setup) ────────────────────────────────────
def _specialist(key: str, name: str, system: str, tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "model": settings.claude_model,
        "system": system,
        "tools": [{"type": "custom", **t} for t in tools],
        "skills": [],
    }


def intelligence_specialist_configs() -> List[Dict[str, Any]]:
    """The 3 investigator sub-agent definitions (Crew / Contract-Wage / Vessel Ops)."""
    return [
        _specialist(
            "crew", "Crew Intel Agent",
            "You are the CREW INTEL investigator in a maritime crew-replacement system. "
            "Your remit: availability, STCW/base certificates, and rank eligibility (the "
            "rank ladder). To evaluate the candidate pool, call the `assess_crew` tool — it "
            "scores every candidate and applies the hard eligibility gates using the "
            "authoritative maritime rules; trust its scores. You may call `rank_ladder` to "
            "explain how a candidate's rank relates to the vacancy. Then report, in 2-3 "
            "sentences, who is eligible and who your strongest candidates are and why. Do "
            "NOT rank across other dimensions — that is the Supervisor's job.",
            [
                {"name": "assess_crew",
                 "description": "Evaluate ALL candidates in the pool on crew eligibility "
                                "(availability, STCW/certs, rank ladder). Takes no arguments; "
                                "returns eligibility + a 0..1 score + top reason per candidate.",
                 "input_schema": _NO_ARGS},
                {"name": "rank_ladder",
                 "description": "Look up a rank's family and ladder (to judge exact/adjacent cover).",
                 "input_schema": {"type": "object", "properties": {"rank": {"type": "string"}},
                                  "required": ["rank"]}},
            ],
        ),
        _specialist(
            "contract", "Contract/Wage Intel Agent",
            "You are the CONTRACT/WAGE INTEL investigator in a maritime crew-replacement "
            "system. Your remit: whether a candidate is commercially acceptable — wage band "
            "fit and MLC-aligned contract period. Call the `assess_contract` tool to score "
            "every candidate using the authoritative wage bands and contract envelope; trust "
            "its scores. You may call `wage_band` to cite the vacancy's band. Then report, in "
            "2-3 sentences, the commercial fit. This dimension is advisory and never gates a "
            "candidate alone.",
            [
                {"name": "assess_contract",
                 "description": "Evaluate ALL candidates on commercial fit (wage band + contract "
                                "envelope). Takes no arguments; returns a 0..1 score + reason per candidate.",
                 "input_schema": _NO_ARGS},
                {"name": "wage_band",
                 "description": "Look up the monthly wage band (USD) and MLC contract envelope for a rank.",
                 "input_schema": {"type": "object", "properties": {"rank": {"type": "string"}},
                                  "required": ["rank"]}},
            ],
        ),
        _specialist(
            "vessel", "Vessel Ops Intel Agent",
            "You are the VESSEL OPS INTEL investigator in a maritime crew-replacement system. "
            "Your remit: vessel-mandated certificates, minimum sea-time, the join port's "
            "departure window, and the L2 graph's port nationality restrictions. Call the "
            "`assess_vessel` tool to score every candidate and apply the hard cert/port gates "
            "using the authoritative rules; trust its scores. You may call `port_facts` to cite "
            "the join port's restrictions and join-by date. Then report, in 2-3 sentences, who "
            "can actually board this vessel at this port and why.",
            [
                {"name": "assess_vessel",
                 "description": "Evaluate ALL candidates on vessel ops (mandated certs, sea-time, "
                                "join window, L2 port restrictions). Takes no arguments; returns "
                                "eligibility + a 0..1 score + reason per candidate.",
                 "input_schema": _NO_ARGS},
                {"name": "port_facts",
                 "description": "Look up a join port's restricted nationalities, join-by date, and "
                                "departure window (from the L2 knowledge graph / its rule data).",
                 "input_schema": {"type": "object", "properties": {"port": {"type": "string"}},
                                  "required": ["port"]}},
            ],
        ),
    ]


COORDINATOR_SYSTEM = """You are the L3 INTELLIGENCE SUPERVISOR for a maritime crew sign-off \
system. You are a ROUTER/COORDINATOR — you NEVER assess candidates yourself.

Your roster of specialist investigator sub-agents:
- Crew Intel Agent      : availability, certificates, rank eligibility
- Contract/Wage Intel Agent : wage band + contract rules for the period
- Vessel Ops Intel Agent : vessel-mandated certs, sea-time, port schedule, port restrictions

Given a vacancy and a candidate pool (already loaded into the investigators' tools), do this:
1. Delegate to ALL THREE investigators IN PARALLEL (spawn them in the same turn).
2. Instruct each to call its assess tool to evaluate EVERY candidate in the pool.
3. When all three have reported, give a 2-3 sentence synthesis of the standout candidates.

Do NOT rank or pick a winner yourself and do NOT invent crew data — the platform fuses the \
investigators' scores deterministically. Keep your messages concise and operational."""


def intelligence_coordinator_config(roster_agent_ids: List[str]) -> Dict[str, Any]:
    """Persisted coordinator (Supervisor) definition; roster = the 3 sub-agent IDs."""
    return {
        "name": COORDINATOR_NAME,
        "model": settings.claude_model,
        "system": COORDINATOR_SYSTEM,
        "tools": [{"type": "agent_toolset_20260401"}],
        "skills": [],
        "multiagent": {
            "type": "coordinator",
            "agents": [{"type": "agent", "id": aid} for aid in roster_agent_ids],
        },
    }


# ── Runtime tool routing ────────────────────────────────────────────────────────
class IntelToolRouter:
    """Resolves the sub-agents' custom-tool calls against the existing investigators.

    The authoritative `InvestigatorReport` for each dimension is produced by running
    the deterministic investigator over the run's pool (cached, so repeated tool calls
    are cheap). `reports()` backfills any dimension the LLM did not trigger, guaranteeing
    the fusion step always sees all three.
    """

    def __init__(self, context: SignOffContext, candidates: List[Dict[str, Any]]):
        self.context = context
        self.candidates = candidates
        self.candidates_by_id = {c["crew_id"]: c for c in candidates}
        self._investigators = {
            "crew": CrewIntel(),
            "contract": ContractWageIntel(),
            "vessel": VesselOpsIntel(),
        }
        self._reports: Dict[str, InvestigatorReport] = {}

    async def _ensure_report(self, key: str) -> InvestigatorReport:
        if key not in self._reports:
            self._reports[key] = await self._investigators[key].investigate(
                self.context, self.candidates
            )
        return self._reports[key]

    async def route(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """Called by ManagedAgentsClient.run_turn on each agent.custom_tool_use."""
        tool_input = tool_input or {}
        key = _ASSESS_TOOL_TO_KEY.get(tool_name)
        if key:
            return self._summarize(await self._ensure_report(key))
        if tool_name == "rank_ladder":
            return _rank_ladder_fact(tool_input.get("rank") or self.context.vacated_rank)
        if tool_name == "wage_band":
            return _wage_band_fact(tool_input.get("rank") or self.context.vacated_rank)
        if tool_name == "port_facts":
            return await _port_facts_fact(tool_input.get("port") or self.context.port)
        log.warning("intel_router.unknown_tool", tool=tool_name)
        return {"error": f"No investigator owns tool '{tool_name}'"}

    def _summarize(self, report: InvestigatorReport) -> Dict[str, Any]:
        """Compact, LLM-friendly view of a dimension's assessments (full report cached)."""
        rows = []
        for cid, a in report.assessments.items():
            crew = self.candidates_by_id.get(cid, {})
            rows.append({
                "crew_id": cid,
                "name": crew.get("name", cid),
                "rank": crew.get("rank"),
                "eligible": a.eligible,
                "score": round(a.score, 3),
                "reason": a.reasons[0] if a.reasons else "",
            })
        rows.sort(key=lambda r: (-r["score"], r["crew_id"]))
        return {
            "dimension": report.investigator,
            "assessed": len(rows),
            "eligible": sum(1 for r in rows if r["eligible"]),
            "candidates": rows,
        }

    async def reports(self) -> List[InvestigatorReport]:
        """The 3 dimension reports, backfilling any the LLM never triggered."""
        for key in ("crew", "contract", "vessel"):
            await self._ensure_report(key)
        return [self._reports["crew"], self._reports["contract"], self._reports["vessel"]]

    def triggered_keys(self) -> List[str]:
        """Dimensions the LLM actually exercised (vs backfilled) — for observability."""
        return list(self._reports.keys())


# ── Read-only fact helpers (the sub-agents' non-scoring tools) ──────────────────
def _rank_ladder_fact(rank: Optional[str]) -> Dict[str, Any]:
    fam = rank_family(rank)
    ladder = {"deck": DECK_LADDER, "engine": ENGINE_LADDER, "rating": RATING_GROUP}.get(fam, [])
    return {"rank": rank, "family": fam, "ladder": ladder}


def _wage_band_fact(rank: Optional[str]) -> Dict[str, Any]:
    return {"rank": rank, "wage_band_usd": wage_band(rank), "contract_envelope_months": STANDARD_CONTRACT}


async def _port_facts_fact(port: Optional[str]) -> Dict[str, Any]:
    facts = await port_restriction_facts(port)
    return {
        "port": port,
        "restricted_nationalities": facts.get("restricted_nationalities", []),
        "join_by": join_by_date(port),
        "departure_window_days": departure_window_days(port),
        "backend": facts.get("backend"),
    }
