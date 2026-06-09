"""
L3 Intelligence-Graph domain rules, as editable DATA (not scattered if/else).

The three L3 investigators (Crew Intel, Contract/Wage Intel, Vessel Ops Intel)
consult this module the same way the Compliance Agent consults
`database.compliance_graph`. Keeping the rules here means:

  * they are a single source of truth the investigators import;
  * the L3 prototype is demoable TODAY with no extra infra (the "fallback" backend
    used across this repo) — when L2's Apache AGE graph (EntityMap/OpsMap/OrgMap)
    lands, these same lookups can be served from Cypher without changing the
    investigators' interface;
  * tests can assert against known, deterministic rule values.

Everything here is plain Python with no DB dependency.
"""
from datetime import date
from typing import Dict, List, Optional

# ── Rank ladder (Crew Intel: rank eligibility) ──────────────────────────────────
# Ordered families so we can judge "exact", "one step", or "unrelated" rank moves.
DECK_LADDER = ["Deck Cadet", "Third Officer", "Second Officer", "Chief Officer", "Master"]
ENGINE_LADDER = ["Engine Cadet", "Fourth Engineer", "Third Engineer", "Second Engineer", "Chief Engineer"]
# Ratings have no cross-coverage (a Cook can't cover a Bosun), so a rating vacancy is
# fillable ONLY by an eligible member of that exact rating.
RATING_GROUP = ["AB Seaman", "Bosun", "Electrician", "Cook", "Pumpman"]


def rank_family(rank: Optional[str]) -> Optional[str]:
    r = rank or ""
    if r in DECK_LADDER:
        return "deck"
    if r in ENGINE_LADDER:
        return "engine"
    if r in RATING_GROUP:
        return "rating"
    return None


def rank_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Steps between two ranks on the same ladder; None if different families.

    0 = exact match, 1 = directly adjacent (acceptable cover), etc.
    """
    for ladder in (DECK_LADDER, ENGINE_LADDER):
        if a in ladder and b in ladder:
            return abs(ladder.index(a) - ladder.index(b))
    if a in RATING_GROUP and b in RATING_GROUP:
        return 0 if a == b else 2  # ratings aren't a strict ladder
    return None


# ── Certificates (Crew Intel + Vessel Ops Intel) ────────────────────────────────
BASE_REQUIRED_CERTS = ["STCW Basic Safety"]
# Certs a vessel/flag mandates for a rank before it will accept the crew aboard.
# Missing one of these is a HARD gate in Vessel Ops Intel.
VESSEL_REQUIRED_CERTS_BY_RANK: Dict[str, List[str]] = {
    "Master": ["STCW Basic Safety", "GMDSS"],
    "Chief Officer": ["STCW Basic Safety", "GMDSS"],
    "Second Officer": ["STCW Basic Safety", "GMDSS"],
    "Third Officer": ["STCW Basic Safety"],
    "Chief Engineer": ["STCW Basic Safety", "High Voltage"],
    "Second Engineer": ["STCW Basic Safety", "High Voltage"],
    "Third Engineer": ["STCW Basic Safety"],
    "Electrician": ["STCW Basic Safety", "High Voltage"],
    # Pumpman (tanker rating) must hold tanker familiarization to board.
    "Pumpman": ["STCW Basic Safety", "Tanker Familiarization"],
}

# Minimum sea-time (years) a vessel expects for a rank.
MIN_EXPERIENCE_BY_RANK: Dict[str, int] = {
    "Master": 12, "Chief Officer": 8, "Second Officer": 4, "Third Officer": 2,
    "Chief Engineer": 12, "Second Engineer": 7, "Third Engineer": 3, "Fourth Engineer": 1,
    "Bosun": 5, "AB Seaman": 2, "Electrician": 4, "Cook": 2,
    "Deck Cadet": 0, "Engine Cadet": 0,
}


def vessel_required_certs(rank: Optional[str]) -> List[str]:
    return VESSEL_REQUIRED_CERTS_BY_RANK.get(rank or "", list(BASE_REQUIRED_CERTS))


def min_experience(rank: Optional[str]) -> int:
    return MIN_EXPERIENCE_BY_RANK.get(rank or "", 2)


# ── Wage bands + contract rules (Contract/Wage Intel) ───────────────────────────
# USD / month (min, max) per rank. Candidate's modelled wage must fall in band.
WAGE_BANDS: Dict[str, tuple] = {
    "Master": (9000, 13000), "Chief Officer": (6500, 9000),
    "Second Officer": (4000, 5500), "Third Officer": (3000, 4200),
    "Chief Engineer": (8500, 12000), "Second Engineer": (6000, 8500),
    "Third Engineer": (3500, 5000), "Fourth Engineer": (2800, 3800),
    "Bosun": (2200, 3000), "AB Seaman": (1500, 2200), "Electrician": (3000, 4500),
    "Cook": (1500, 2300), "Deck Cadet": (800, 1200), "Engine Cadet": (800, 1200),
    "Pumpman": (2000, 2800),
}

# Grade premium applied to the band midpoint to model a candidate's expected wage.
GRADE_MULTIPLIER: Dict[str, float] = {
    "Grade A": 1.10, "Grade B": 1.00, "Grade C": 0.92, "Grade D": 0.85,
}

# Standard MLC-2006-aligned contract envelope (months).
STANDARD_CONTRACT = {"min_months": 4, "max_months": 9, "mlc_max_months": 11}


def wage_band(rank: Optional[str]) -> Optional[tuple]:
    return WAGE_BANDS.get(rank or "")


def expected_wage(rank: Optional[str], grade: Optional[str]) -> Optional[int]:
    """Modelled monthly wage = band midpoint × grade premium."""
    band = wage_band(rank)
    if not band:
        return None
    midpoint = (band[0] + band[1]) / 2
    return int(round(midpoint * GRADE_MULTIPLIER.get(grade or "", 1.0)))


# ── Port schedule (Vessel Ops Intel: can the candidate join in time?) ───────────
# Days from "today" until the vessel departs the port — i.e. the join-by window.
PORT_DEPARTURE_DAYS: Dict[str, int] = {
    "Singapore": 6, "Rotterdam": 8, "Houston": 10, "Dubai": 5, "Shanghai": 7,
    "Hamburg": 9, "Piraeus": 6, "Manila": 4, "Mumbai": 5, "Busan": 7,
}
DEFAULT_DEPARTURE_DAYS = 7
# Travel lead time (days) needed if the candidate is NOT already at the join port.
RELOCATION_LEAD_DAYS = 3


def departure_window_days(port: Optional[str]) -> int:
    return PORT_DEPARTURE_DAYS.get(port or "", DEFAULT_DEPARTURE_DAYS)


def join_by_date(port: Optional[str]) -> str:
    from datetime import timedelta
    return (date.today() + timedelta(days=departure_window_days(port))).isoformat()
