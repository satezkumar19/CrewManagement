"""
Crew Intel investigator — availability, certs, rank eligibility.

HARD gates (disqualify): unavailable, or rank not eligible (wrong family, or more
than one step off on the rank ladder). Soft signals (score): cert validity, rank
exactness, and sea-time experience.
"""
from typing import Any, Dict

from agents.intelligence import graph_gateway
from agents.intelligence.base_investigator import BaseInvestigator
from agents.intelligence.schemas import Assessment, SignOffContext
from database.intel_rules import BASE_REQUIRED_CERTS, rank_distance, rank_family


class CrewIntel(BaseInvestigator):
    key = "crew"
    name = "Crew Intel"

    def _assess(self, context: SignOffContext, crew: Dict[str, Any],
                applied: Dict[str, Any], prep: Dict[str, Any]) -> Assessment:
        applied.setdefault("dimension", "availability + certs + rank eligibility")
        applied.setdefault("l2_backend", graph_gateway.backend())
        crew_id = crew["crew_id"]
        reasons = []
        signals: Dict[str, Any] = {}
        score = 0.0
        eligible = True

        # ── Availability (hard gate) ──────────────────────────────────────────────
        available = (crew.get("status") == "Available") or (crew.get("availability") == "Available")
        signals["available"] = available
        if not available:
            return Assessment(self.name, crew_id, 0.0, False, signals,
                              [f"Unavailable (status={crew.get('status')})"])
        reasons.append("Available for assignment")

        # ── Rank eligibility (hard gate + score) ──────────────────────────────────
        dist = rank_distance(crew.get("rank"), context.vacated_rank)
        same_family = rank_family(crew.get("rank")) == rank_family(context.vacated_rank)
        signals["rank"] = crew.get("rank")
        signals["rank_distance"] = dist
        if dist is None or not same_family or dist > 1:
            return Assessment(self.name, crew_id, 0.0, False, signals,
                              [f"Rank '{crew.get('rank')}' not eligible to cover '{context.vacated_rank}'"])
        if dist == 0:
            score += 0.55
            reasons.append(f"Exact rank match ({crew.get('rank')})")
        else:
            score += 0.35
            reasons.append(f"Adjacent rank cover ({crew.get('rank')} → {context.vacated_rank})")

        # ── STCW + base certificates (score) ──────────────────────────────────────
        stcw = crew.get("stcw_status", "Unknown")
        signals["stcw_status"] = stcw
        if stcw == "Valid":
            score += 0.25
            reasons.append("STCW valid")
        elif stcw == "Expiring Soon":
            score += 0.10
            reasons.append("STCW expiring soon")
        else:
            reasons.append("STCW invalid/missing")

        held = set(crew.get("certifications") or [])
        missing_base = [c for c in BASE_REQUIRED_CERTS if c not in held]
        signals["missing_base_certs"] = missing_base
        if not missing_base:
            score += 0.10
        else:
            reasons.append(f"Missing base certs: {', '.join(missing_base)}")

        # L2 consult: the rank's required SAFETY certs per the L2 graph's REQUIRES
        # edges (surfaced as a signal for transparency; the hard cert gate lives in
        # Vessel Ops Intel so a candidate is never double-gated on certificates).
        l2_safety = graph_gateway.safety_certs_for_rank(context.vacated_rank)
        signals["l2_required_safety_certs"] = l2_safety
        signals["l2_missing_safety_certs"] = [c for c in l2_safety if c not in held]

        # ── Experience (score) ────────────────────────────────────────────────────
        exp = crew.get("experience_years", 0) or 0
        signals["experience_years"] = exp
        score += self._clamp(exp / 20.0) * 0.10
        if exp >= 10:
            reasons.append(f"{exp} yrs experience")

        return Assessment(self.name, crew_id, self._clamp(score), True, signals, reasons)
