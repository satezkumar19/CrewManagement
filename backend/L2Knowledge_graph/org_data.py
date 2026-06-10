"""
OrgMap source data — the organizational hierarchy ABOVE the vessel.

The crew table has no company / fleet / manning information, so (exactly as the L2
design §5.2 anticipates) that structure is authored here and the OrgMap builder
(org_map.py) overlays it onto the EXISTING EntityMap `Vessel` nodes — it never
re-creates vessels (§5.3 shared-node contract).

Three things are defined:
  1. ORG_TREE   — Company → Fleet → [Vessel]   (vessels match EntityMap by name)
  2. MANNING    — Rank → required headcount per vessel (standard manning template)
  3. helpers    — flat views the builder and queries consume.

Vessel names MUST match mock_data.crew_data.VESSELS so the OPERATES edges attach to
real EntityMap vessel nodes.
"""
from typing import Dict, List, Tuple

# ── 1. Ownership hierarchy: Company → Fleet → Vessels ───────────────────────────
# The 5 EntityMap vessels distributed across 2 companies / 4 fleets.
ORG_TREE: Dict[str, Dict[str, List[str]]] = {
    "Oceanic Shipping Lines": {
        "Pacific Fleet": ["MV Pacific Star", "MV Indian Ocean Pride"],
        "Atlantic Fleet": ["MV Atlantic Voyager"],
    },
    "Meridian Maritime": {
        "Tanker Division": ["MT Crude Titan"],
        "Mediterranean Fleet": ["MV Mediterranean Queen"],
    },
}

# ── 2. Manning template: required headcount per rank, per vessel ────────────────
# A standard manning scale applied to every vessel (vessels aren't typed in the
# seed data, so one template fits all). Ranks are a subset of mock_data RANKS.
MANNING: Dict[str, int] = {
    "Master": 1,
    "Chief Officer": 1,
    "Second Officer": 1,
    "Third Officer": 1,
    "Chief Engineer": 1,
    "Second Engineer": 1,
    "Third Engineer": 1,
    "Bosun": 1,
    "AB Seaman": 2,
    "Electrician": 1,
    "Cook": 1,
}


# ── 2b. Chain of command: which rank reports to which ───────────────────────────
# The shipboard reporting hierarchy (standard merchant-navy structure). The Master
# sits at the top; the Deck department reports up through the Chief Officer and the
# Engine department through the Chief Engineer. `None` marks the top of the tree.
# Covers the MANNING ranks plus the trainee/junior ranks that appear in the crew data
# (Cadets, Fourth Engineer) so every onboard role slots into the tree rather than
# dangling at the top. Every key must exist as a Rank node (MANNING ranks + crewed ranks).
RANK_REPORTS_TO: Dict[str, str | None] = {
    "Master": None,                          # top of the shipboard hierarchy
    # Deck department
    "Chief Officer": "Master",
    "Second Officer": "Chief Officer",
    "Third Officer": "Second Officer",
    "Deck Cadet": "Third Officer",
    "Bosun": "Chief Officer",
    "AB Seaman": "Bosun",
    # Engine department
    "Chief Engineer": "Master",
    "Second Engineer": "Chief Engineer",
    "Third Engineer": "Second Engineer",
    "Fourth Engineer": "Third Engineer",
    "Engine Cadet": "Fourth Engineer",
    "Electrician": "Chief Engineer",
    # Catering
    "Cook": "Master",
}


# ── 2c. Deliberate manning shortfalls ───────────────────────────────────────────
# The OrgMap builder fills each vessel toward its MANNING template with a standing
# complement so the fleet reads as properly crewed. These (vessel, rank) positions are
# left UNFILLED on purpose, so the manning-gap query still surfaces a few realistic
# shortages (and the role view still shows some red) instead of an all-zero board.
MANNING_GAPS: Dict[Tuple[str, str], int] = {
    ("MV Indian Ocean Pride", "AB Seaman"): 1,
    ("MV Indian Ocean Pride", "Bosun"): 1,
    ("MT Crude Titan", "Electrician"): 1,
    ("MV Atlantic Voyager", "Cook"): 1,
}


# ── 3. Flat views ───────────────────────────────────────────────────────────────
def companies() -> List[str]:
    return list(ORG_TREE.keys())


def fleets() -> List[str]:
    return [fleet for company in ORG_TREE.values() for fleet in company]


def company_of_fleet() -> Dict[str, str]:
    """fleet name -> owning company name."""
    return {fleet: company for company, fleets_ in ORG_TREE.items() for fleet in fleets_}


def fleet_of_vessel() -> Dict[str, str]:
    """vessel name -> operating fleet name."""
    out: Dict[str, str] = {}
    for fleets_ in ORG_TREE.values():
        for fleet, vessels in fleets_.items():
            for v in vessels:
                out[v] = fleet
    return out


def vessels() -> List[str]:
    return list(fleet_of_vessel().keys())


def manning_ranks() -> List[str]:
    return list(MANNING.keys())


def rank_reports_to() -> Dict[str, str | None]:
    """rank name -> the rank it reports to (None for the Master, the top of the tree)."""
    return dict(RANK_REPORTS_TO)


def manning_gaps() -> Dict[Tuple[str, str], int]:
    """(vessel, rank) -> positions deliberately left unfilled by the standing complement."""
    return dict(MANNING_GAPS)
