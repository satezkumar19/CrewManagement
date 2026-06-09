"""
Contract/Wage Intel investigator — applicable rules for the period.

Advisory dimension (no hard gates by default): it scores how cleanly a candidate
fits the wage band and the contract envelope for the requested period, and surfaces
the applicable rules so operators see the commercial rationale.
"""
from typing import Any, Dict

from agents.intelligence.base_investigator import BaseInvestigator
from agents.intelligence.schemas import Assessment, SignOffContext
from database.intel_rules import STANDARD_CONTRACT, expected_wage, wage_band


class ContractWageIntel(BaseInvestigator):
    key = "contract"
    name = "Contract/Wage Intel"

    def _assess(self, context: SignOffContext, crew: Dict[str, Any],
                applied: Dict[str, Any], prep: Dict[str, Any]) -> Assessment:
        crew_id = crew["crew_id"]
        # The vacancy's wage band + contract envelope is the rule set we apply.
        band = wage_band(context.vacated_rank)
        period = context.contract_period_months
        applied.setdefault("vacated_rank", context.vacated_rank)
        applied.setdefault("wage_band_usd", band)
        applied.setdefault("contract_rules", STANDARD_CONTRACT)
        applied.setdefault("period_months", period)

        reasons = []
        signals: Dict[str, Any] = {}
        score = 0.0

        # ── Wage fit against the vacancy's band ───────────────────────────────────
        cand_wage = expected_wage(context.vacated_rank, crew.get("grade"))
        signals["expected_wage_usd"] = cand_wage
        signals["wage_band_usd"] = band
        if band and cand_wage is not None:
            lo, hi = band
            if lo <= cand_wage <= hi:
                score += 0.6
                reasons.append(f"Wage ${cand_wage} within band ${lo}-${hi}")
            elif cand_wage < lo:
                score += 0.5
                reasons.append(f"Wage ${cand_wage} below band — cost-favourable")
            else:
                over = round((cand_wage - hi) / hi * 100)
                score += max(0.1, 0.4 - over / 100)
                reasons.append(f"Wage ${cand_wage} is {over}% over band — budget review")
        else:
            score += 0.3
            reasons.append("No wage band on file — flat assessment")

        # ── Contract period within the MLC-aligned envelope ───────────────────────
        signals["period_months"] = period
        if STANDARD_CONTRACT["min_months"] <= period <= STANDARD_CONTRACT["max_months"]:
            score += 0.4
            reasons.append(f"{period}-month contract within standard envelope")
        elif period <= STANDARD_CONTRACT["mlc_max_months"]:
            score += 0.25
            reasons.append(f"{period}-month contract within MLC max")
        else:
            score += 0.05
            reasons.append(f"{period}-month contract exceeds MLC max {STANDARD_CONTRACT['mlc_max_months']}")

        return Assessment(self.name, crew_id, self._clamp(score), True, signals, reasons)
