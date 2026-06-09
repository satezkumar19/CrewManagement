"""
Ranking fusion — combine the three investigators' per-candidate assessments into a
single ranked shortlist with a combined rationale.

Rules:
  * A candidate is DISQUALIFIED if ANY investigator set eligible=False (hard gate)
    — availability/rank (Crew Intel) or vessel-mandated certs (Vessel Ops Intel).
  * Surviving candidates are scored by a weighted blend of the three dimensions.
  * Rationale = the top reason(s) from each dimension, so an operator sees WHY a
    candidate placed where they did.

Deterministic by design (no randomness) so the L3 test scenarios have stable
expected output.
"""
from typing import Dict, List

from agents.intelligence.schemas import InvestigatorReport, RankedCandidate

# Dimension weights — Crew eligibility dominates, vessel ops next, commercial last.
WEIGHTS: Dict[str, float] = {"crew": 0.50, "vessel": 0.30, "contract": 0.20}


def fuse(
    reports: List[InvestigatorReport],
    candidates_by_id: Dict[str, dict],
    top_n: int = 3,
) -> List[RankedCandidate]:
    """Fuse investigator reports → top-N ranked candidates."""
    by_key = {_key_for(r.investigator): r for r in reports}
    crew_ids = candidates_by_id.keys()

    ranked: List[RankedCandidate] = []
    for crew_id in crew_ids:
        dim_scores: Dict[str, float] = {}
        rationale: List[str] = []
        disqualified = False

        for key, report in by_key.items():
            a = report.assessments.get(crew_id)
            if a is None:
                continue
            if not a.eligible:
                disqualified = True
                # Capture the gate reason so "why not" is explainable too.
                rationale = a.reasons[:1]
                break
            dim_scores[key] = a.score
            if a.reasons:
                rationale.append(a.reasons[0])  # top reason per dimension

        if disqualified:
            continue

        fused = sum(WEIGHTS.get(k, 0.0) * dim_scores.get(k, 0.0) for k in WEIGHTS)
        crew = candidates_by_id[crew_id]
        ranked.append(RankedCandidate(
            rank_position=0,  # filled after sort
            crew_id=crew_id,
            name=crew.get("name", crew_id),
            rank=crew.get("rank", ""),
            grade=crew.get("grade"),
            nationality=crew.get("nationality"),
            port=crew.get("port"),
            score=round(fused * 100, 1),
            rationale=rationale,
            dimension_scores={k: round(v, 3) for k, v in dim_scores.items()},
        ))

    # Deterministic ordering: score desc, then crew_id asc as a stable tiebreak.
    ranked.sort(key=lambda c: (-c.score, c.crew_id))
    top = ranked[:top_n]
    for i, c in enumerate(top, start=1):
        c.rank_position = i
    return top


def _key_for(investigator_name: str) -> str:
    name = investigator_name.lower()
    if "crew" in name:
        return "crew"
    if "contract" in name or "wage" in name:
        return "contract"
    if "vessel" in name:
        return "vessel"
    return name
