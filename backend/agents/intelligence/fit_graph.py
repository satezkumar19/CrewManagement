"""
L3 fit-graph builder — turns one Supervisor run into a REAL node/edge graph.

This is "Sense 1" of making L3 a graph: rather than storing the run as a flat list,
we derive a graph the frontend can render and L4 can later persist as precedent. It
is a pure transform of data the Supervisor already computed (no new infra, no L2
dependency) — the same shape as the Compliance context graph so the existing
React-Flow renderer style applies:

    (Vacancy) ──ASSESSED──▶ (Candidate) ──SCORED──▶ (Dimension) ──L2──▶ (L2 Fact)
                            (disqualified candidates link to the blocking dimension
                             with the gate reason as the edge label)

Node shape  : {id, type, label, sub, status, x, y}
Edge shape  : {id, source, target, label, status}
status      : "ok" | "warn" | "block"   (matches the compliance graph vocabulary)
"""
from typing import Any, Dict, List, Optional

from agents.intelligence.ranking import _key_for
from agents.intelligence.schemas import InvestigatorReport, RankedCandidate, SignOffContext

# Left-to-right layered layout. ReactFlow fitView handles final scaling.
_COL = {"vacancy": 0, "candidate": 240, "dimension": 520, "fact": 780}
_GAP = 90
_MAX_CANDIDATES = 12  # keep the graph readable; note any overflow on the vacancy node

_DIMENSIONS = [
    ("crew", "Crew Intel"),
    ("vessel", "Vessel Ops"),
    ("contract", "Contract/Wage"),
]


def _column_ys(count: int, max_rows: int) -> List[float]:
    """Vertically centre `count` items within a `max_rows`-tall canvas."""
    mid = (max_rows - 1) / 2 * _GAP
    start = mid - (count - 1) / 2 * _GAP
    return [start + i * _GAP for i in range(count)]


def _short(text: str, limit: int = 46) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_fit_graph(
    context: SignOffContext,
    candidates_by_id: Dict[str, Dict[str, Any]],
    reports: List[InvestigatorReport],
    ranked: List[RankedCandidate],
    backend: str = "fallback",
) -> Dict[str, Any]:
    """Derive the L3 fit graph from one Supervisor run."""
    reports_by_key = {_key_for(r.investigator): r for r in reports}
    applied_by_key = {k: r.applied for k, r in reports_by_key.items()}
    ranked_by_id = {c.crew_id: c for c in ranked}

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    # ── Classify & order candidates: ranked first, then disqualified, then rest ──
    def _disqualifying(crew_id: str):
        """Return (dimension_key, reason) for the first hard gate that failed, else None."""
        for key in ("crew", "vessel", "contract"):
            rep = reports_by_key.get(key)
            a = rep.assessments.get(crew_id) if rep else None
            if a is not None and not a.eligible:
                return key, (a.reasons[0] if a.reasons else "ineligible")
        return None

    ordered: List[str] = list(ranked_by_id.keys())
    disq: List[str] = []
    rest: List[str] = []
    for crew_id in candidates_by_id:
        if crew_id in ranked_by_id:
            continue
        (disq if _disqualifying(crew_id) else rest).append(crew_id)
    ordered += disq + rest
    overflow = max(0, len(ordered) - _MAX_CANDIDATES)
    ordered = ordered[:_MAX_CANDIDATES]

    # ── Canvas height drives vertical centring of every column ──
    max_rows = max(len(ordered), len(_DIMENSIONS) + 2, 1)

    # ── Vacancy node (single, centred in its column) ──
    vac_sub = " · ".join(p for p in [context.port, context.vessel] if p) or None
    if overflow:
        vac_sub = f"{vac_sub} (+{overflow} more)" if vac_sub else f"+{overflow} more candidates"
    nodes.append({
        "id": "vacancy",
        "type": "Vacancy",
        "label": context.vacated_rank or "Vacancy",
        "sub": vac_sub,
        "status": "block" if not ranked else "ok",
        "x": _COL["vacancy"],
        "y": _column_ys(1, max_rows)[0],
    })

    # ── Dimension nodes (always the three lenses) ──
    dim_ys = _column_ys(len(_DIMENSIONS), max_rows)
    for (key, label), y in zip(_DIMENSIONS, dim_ys):
        nodes.append({
            "id": f"dim_{key}",
            "type": "Dimension",
            "label": label,
            "sub": f"weight {int({'crew': 50, 'vessel': 30, 'contract': 20}[key])}%",
            "status": "ok",
            "x": _COL["dimension"],
            "y": y,
        })

    # ── Candidate nodes + their edges ──
    cand_ys = _column_ys(len(ordered), max_rows)
    for crew_id, y in zip(ordered, cand_ys):
        crew = candidates_by_id.get(crew_id, {})
        rc = ranked_by_id.get(crew_id)
        node_id = f"cand_{crew_id}"
        gate = _disqualifying(crew_id)

        if rc is not None:
            status = "ok"
            sub = f"#{rc.rank_position} · {rc.score}"
            vac_label, vac_status = f"shortlist #{rc.rank_position}", "ok"
        elif gate is not None:
            status = "block"
            sub = f"{crew.get('rank', '')} · {crew.get('port', '') or '—'}"
            vac_label, vac_status = "disqualified", "block"
        else:
            status = "warn"
            sub = f"{crew.get('rank', '')} · eligible"
            vac_label, vac_status = "assessed", "warn"

        nodes.append({
            "id": node_id,
            "type": "Candidate",
            "label": crew.get("name", crew_id),
            "sub": sub,
            "status": status,
            "x": _COL["candidate"],
            "y": y,
        })
        edges.append({
            "id": f"e_vac_{crew_id}",
            "source": "vacancy",
            "target": node_id,
            "label": vac_label,
            "status": vac_status,
        })

        if rc is not None:
            # Scored edges to each dimension — the candidate's per-lens breakdown.
            for key, _ in _DIMENSIONS:
                score = rc.dimension_scores.get(key)
                if score is None:
                    continue
                pct = round(score * 100)
                edges.append({
                    "id": f"e_{crew_id}_{key}",
                    "source": node_id,
                    "target": f"dim_{key}",
                    "label": f"{pct}",
                    "status": "ok" if score >= 0.5 else "warn",
                })
        elif gate is not None:
            # One edge to the blocking dimension, carrying the gate reason.
            gkey, reason = gate
            edges.append({
                "id": f"e_{crew_id}_block",
                "source": node_id,
                "target": f"dim_{gkey}",
                "label": _short(reason),
                "status": "block",
            })

    # ── L2 fact nodes — tie the dimensions back to the L2 graph reads ──
    fact_nodes: List[Dict[str, Any]] = []

    vessel_applied = applied_by_key.get("vessel", {})
    restricted = vessel_applied.get("l2_port_restricted_nationalities") or []
    join_port = vessel_applied.get("join_port") or context.port or "port"
    fact_nodes.append({
        "id": "fact_port",
        "type": "L2Fact",
        "label": "Port rules",
        "sub": (f"{join_port}: restricts {', '.join(restricted)}" if restricted
                else f"{join_port}: no restrictions"),
        "status": "warn" if restricted else "ok",
        "_from": "dim_vessel",
        "_edge": "L2 RESTRICTS",
    })

    # Required safety certs come from the L2 REQUIRES edges (surfaced per-candidate
    # by Crew Intel); pull them off any crew assessment's signals.
    crew_rep = reports_by_key.get("crew")
    safety_certs: List[str] = []
    if crew_rep:
        for a in crew_rep.assessments.values():
            certs = a.signals.get("l2_required_safety_certs")
            if certs:
                safety_certs = list(certs)
                break
    fact_nodes.append({
        "id": "fact_certs",
        "type": "L2Fact",
        "label": "Safety certs",
        "sub": ", ".join(safety_certs[:3]) if safety_certs else "—",
        "status": "ok",
        "_from": "dim_crew",
        "_edge": "L2 REQUIRES",
    })

    fact_ys = _column_ys(len(fact_nodes), max_rows)
    for fact, y in zip(fact_nodes, fact_ys):
        nodes.append({
            "id": fact["id"], "type": fact["type"], "label": fact["label"],
            "sub": fact["sub"], "status": fact["status"], "x": _COL["fact"], "y": y,
        })
        edges.append({
            "id": f"e_{fact['_from']}_{fact['id']}",
            "source": fact["_from"],
            "target": fact["id"],
            "label": fact["_edge"],
            "status": fact["status"],
        })

    # Backend label: prefer what an investigator actually recorded.
    resolved_backend = (
        vessel_applied.get("l2_backend")
        or applied_by_key.get("crew", {}).get("l2_backend")
        or backend
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "backend": resolved_backend,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
