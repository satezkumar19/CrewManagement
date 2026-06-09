"""
Vessel Ops Intel investigator — vessel requirements + port schedule.

This investigator is wired to the **L2 compliance graph**: in `_prepare()` it pulls
the join port's restricted nationalities (L2's multi-hop `Port-[:RESTRICTS]->Country`
fact) once per run, via `graph_gateway` (which serves it from the AGE graph when
`graph_backend=age`, else from the rule data L2 is seeded from).

HARD gates (disqualify): missing a vessel-mandated certificate, OR the L2 graph says
the candidate's nationality is restricted at the join port without a valid visa.
Soft signals (score): meets the vessel's minimum sea-time, and whether the candidate
can join before the vessel departs (same port vs relocation within the join window).
"""
from typing import Any, Dict

from agents.intelligence import graph_gateway
from agents.intelligence.base_investigator import BaseInvestigator
from agents.intelligence.schemas import Assessment, SignOffContext
from database.intel_rules import (
    departure_window_days,
    join_by_date,
    min_experience,
    vessel_required_certs,
    RELOCATION_LEAD_DAYS,
)


class VesselOpsIntel(BaseInvestigator):
    key = "vessel"
    name = "Vessel Ops Intel"

    async def _prepare(self, context: SignOffContext) -> Dict[str, Any]:
        # One L2 read per run (per-port, not per-candidate): the join port's
        # restricted nationalities + min medical validity, from the L2 graph.
        return {"port_facts": await graph_gateway.port_restriction_facts(context.port)}

    def _assess(self, context: SignOffContext, crew: Dict[str, Any],
                applied: Dict[str, Any], prep: Dict[str, Any]) -> Assessment:
        crew_id = crew["crew_id"]
        required = vessel_required_certs(context.vacated_rank)
        min_exp = min_experience(context.vacated_rank)
        window = departure_window_days(context.port)
        port_facts = prep.get("port_facts", {})
        restricted = port_facts.get("restricted_nationalities", [])
        applied.setdefault("vessel", context.vacated_rank and context.vessel)
        applied.setdefault("required_certs", required)
        applied.setdefault("min_experience_years", min_exp)
        applied.setdefault("join_port", context.port)
        applied.setdefault("join_by", join_by_date(context.port))
        applied.setdefault("departure_window_days", window)
        applied.setdefault("l2_backend", port_facts.get("backend"))
        applied.setdefault("l2_port_restricted_nationalities", restricted)

        reasons = []
        signals: Dict[str, Any] = {}
        score = 0.0

        # ── Vessel-mandated certs (hard gate) ─────────────────────────────────────
        held = set(crew.get("certifications") or [])
        missing = [c for c in required if c not in held]
        signals["required_certs"] = required
        signals["missing_required_certs"] = missing
        if missing:
            return Assessment(self.name, crew_id, 0.0, False, signals,
                              [f"Missing vessel-mandated certs for {context.vacated_rank}: {', '.join(missing)}"])
        reasons.append("Holds all vessel-mandated certs")
        score += 0.45

        # ── L2 graph: port nationality restriction (hard gate) ────────────────────
        # The headline multi-hop check from the L2 compliance graph:
        #   (:Port {join})-[:RESTRICTS]->(:Country {candidate nationality})
        # A restricted nationality without a valid visa cannot board here.
        nationality = crew.get("nationality")
        visa = crew.get("visa_status", "Unknown")
        signals["nationality"] = nationality
        signals["l2_backend"] = port_facts.get("backend")
        if nationality in restricted:
            if visa != "Valid":
                return Assessment(self.name, crew_id, 0.0, False, signals, [
                    f"L2 graph: {nationality} nationals are restricted at {context.port} "
                    f"(Port-RESTRICTS->Country) and visa is '{visa}' — cannot board"
                ])
            reasons.append(f"{nationality} is visa-restricted at {context.port} but visa valid [L2 graph]")

        # ── Sea-time minimum (score) ──────────────────────────────────────────────
        exp = crew.get("experience_years", 0) or 0
        signals["experience_years"] = exp
        signals["min_experience_years"] = min_exp
        if exp >= min_exp:
            score += 0.25
            reasons.append(f"Meets {min_exp}-yr sea-time minimum")
        else:
            score += 0.05
            reasons.append(f"Below {min_exp}-yr sea-time minimum ({exp} yrs)")

        # ── Can the candidate join before the vessel sails? (score) ───────────────
        cand_port = crew.get("port")
        signals["candidate_port"] = cand_port
        signals["join_port"] = context.port
        signals["departure_window_days"] = window
        if context.port and cand_port and cand_port.lower() == context.port.lower():
            score += 0.30
            reasons.append(f"Already at join port ({context.port}) — no relocation")
        elif window >= RELOCATION_LEAD_DAYS:
            score += 0.18
            reasons.append(f"Can relocate to {context.port} within {window}-day window")
        else:
            score += 0.05
            reasons.append(f"Tight join window ({window}d) — relocation risk")

        return Assessment(self.name, crew_id, self._clamp(score), True, signals, reasons)
