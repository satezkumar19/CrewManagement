"""
L2 Knowledge Graph — OrgMap dimension (organizational hierarchy overlay).

OrgMap is L2 dimension 3. It models ownership/manning structure ABOVE the vessel and
overlays it on the EXISTING EntityMap graph (per the L2 design §5.2). It introduces
three new node labels and four edge types, and — critically (§5.3) — it MATCHes the
existing `Vessel` and `Crew` nodes and MERGEs only edges / new-label nodes. It never
re-creates Crew/Vessel, so there is no data duplication across dimensions.

    (:Company)-[:OWNS]->(:Fleet)-[:OPERATES]->(:Vessel)   ownership hierarchy
    (:Vessel)-[:REQUIRES_RANK {count}]->(:Rank)           manning requirement per ship
    (:Crew)-[:HAS_RANK]->(:Rank)                          crew rank promoted to a node

Headline query (§5.2): "how many <rank> does Company A's fleet need vs. have?" —
manning_gap() compares REQUIRES_RANK counts against crew ASSIGNED_TO the fleet's vessels.

Requires GRAPH_BACKEND=age (the overlay lives in the AGE `maritime` graph, like EntityMap).
"""
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

import structlog

from L2Knowledge_graph import org_data
from L2Knowledge_graph.graph_db import GRAPH_NAME, age_enabled, ensure_graph, run_cypher
from database.crew_repository import get_sign_off_crew, get_sign_on_crew

log = structlog.get_logger()

# New node labels / edge types OrgMap introduces (Vessel + Crew are reused from EntityMap).
ORG_LABELS = ["Company", "Fleet", "Rank"]
ORG_EDGES = ["OWNS", "OPERATES", "REQUIRES_RANK", "HAS_RANK"]


def _q(value: Any) -> str:
    """Escape a value for safe inlining as a Cypher single-quoted literal (AGE has no
    client-side bind parameters — same approach as entity_map._q)."""
    return str(value if value is not None else "").replace("\\", "\\\\").replace("'", "\\'")


def _in_list(names: List[str]) -> str:
    """Render a Python list of strings as a Cypher list literal: ['a','b']."""
    return "[" + ", ".join(f"'{_q(n)}'" for n in names) + "]"


# ── Build / seed ────────────────────────────────────────────────────────────────


async def build_org_map() -> Dict[str, Any]:
    """(Re)build the OrgMap overlay in AGE. Idempotent (all MERGE). Requires the
    EntityMap to be seeded first — OrgMap MATCHes its Vessel/Crew nodes."""
    if not age_enabled():
        raise RuntimeError(
            "OrgMap requires the AGE backend. Set GRAPH_BACKEND=age and seed EntityMap "
            "first (python -m L2Knowledge_graph.scripts.seed_entity_map), then re-run."
        )
    await ensure_graph()

    # 1. Company → Fleet (OWNS) and Fleet → Vessel (OPERATES). Vessel is MATCHed.
    for company, fleets in org_data.ORG_TREE.items():
        await run_cypher(f"MERGE (:Company {{name:'{_q(company)}'}})")
        for fleet, vessels in fleets.items():
            await run_cypher(f"MERGE (:Fleet {{name:'{_q(fleet)}'}})")
            await run_cypher(
                f"MATCH (co:Company {{name:'{_q(company)}'}}), (f:Fleet {{name:'{_q(fleet)}'}}) "
                f"MERGE (co)-[:OWNS]->(f)"
            )
            for vessel in vessels:
                # MATCH the existing EntityMap vessel — never create it here.
                await run_cypher(
                    f"MATCH (f:Fleet {{name:'{_q(fleet)}'}}), (v:Vessel {{name:'{_q(vessel)}'}}) "
                    f"MERGE (f)-[:OPERATES]->(v)"
                )

    # 2. Manning requirement per vessel: (:Vessel)-[:REQUIRES_RANK {count}]->(:Rank).
    for vessel in org_data.vessels():
        for rank, count in org_data.MANNING.items():
            # NB: the edge property is `required`, not `count` — `count` collides with
            # the Cypher COUNT() function in AGE and fails to parse.
            await run_cypher(
                f"MERGE (r:Rank {{name:'{_q(rank)}'}}) "
                f"WITH r MATCH (v:Vessel {{name:'{_q(vessel)}'}}) "
                f"MERGE (v)-[req:REQUIRES_RANK]->(r) SET req.required = {int(count)}"
            )

    # 3. Promote each crew's rank to a shared Rank node: (:Crew)-[:HAS_RANK]->(:Rank).
    sign_on = await get_sign_on_crew()
    sign_off = await get_sign_off_crew()
    for c in sign_on + sign_off:
        rank = c.get("rank")
        cid = c.get("crew_id")
        if not rank or not cid:
            continue
        await run_cypher(
            f"MERGE (r:Rank {{name:'{_q(rank)}'}}) "
            f"WITH r MATCH (c:Crew {{crew_id:'{_q(cid)}'}}) "
            f"MERGE (c)-[:HAS_RANK]->(r)"
        )

    summary = await org_map_summary()
    log.info("org_map.built", **summary["nodes"], **{f"edge_{k}": v for k, v in summary["edges"].items()})
    return summary


# ── Query interface ─────────────────────────────────────────────────────────────


async def org_map_summary() -> Dict[str, Any]:
    """Per-label node counts and per-type edge counts for the OrgMap overlay
    (plus the reused Vessel/Crew counts), mirroring entity_map_summary()."""
    node_rows = await run_cypher("MATCH (n) RETURN {label: labels(n)[0]} AS v")
    edge_rows = await run_cypher("MATCH ()-[r]->() RETURN {t: type(r)} AS v")
    node_counts = Counter(r.get("label") for r in node_rows if isinstance(r, dict))
    edge_counts = Counter(r.get("t") for r in edge_rows if isinstance(r, dict))

    labels = ORG_LABELS + ["Vessel"]
    nodes = {lbl: node_counts.get(lbl, 0) for lbl in labels}
    edges = {et: edge_counts.get(et, 0) for et in ORG_EDGES}
    return {
        "graph": GRAPH_NAME,
        "dimension": "OrgMap",
        "labels": labels,
        "edge_types": ORG_EDGES,
        "nodes": nodes,
        "edges": edges,
        "companies": org_data.companies(),
        "fleets": org_data.fleets(),
        "total_nodes": sum(nodes.values()),
        "total_edges": sum(edges.values()),
    }


async def org_structure() -> Dict[str, Any]:
    """The Company → Fleet → Vessel hierarchy as a React-Flow-ready {nodes, edges}
    payload (same envelope as EntityMap's subgraph), so the graph UI can render it."""
    # NB: query the two hops separately. A single two-edge path
    # (Company)-[:OWNS]->(Fleet)-[:OPERATES]->(Vessel) triggers AGE's edge-uniqueness
    # enforcement, which is missing in this AGE build (apache/age 1.6.0 / PG16).
    owns_rows = await run_cypher(
        "MATCH (co:Company)-[:OWNS]->(f:Fleet) RETURN {co: co.name, f: f.name} AS v"
    )
    op_rows = await run_cypher(
        "MATCH (f:Fleet)-[:OPERATES]->(v:Vessel) RETURN {f: f.name, v: v.name} AS v"
    )
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    for r in owns_rows:
        if not isinstance(r, dict):
            continue
        co, f = r.get("co"), r.get("f")
        if not (co and f):
            continue
        co_id, f_id = f"co:{co}", f"f:{f}"
        nodes.setdefault(co_id, {"id": co_id, "type": "Company", "label": co})
        nodes.setdefault(f_id, {"id": f_id, "type": "Fleet", "label": f})
        edges.setdefault(f"{co_id}->{f_id}", {"id": f"{co_id}->{f_id}", "source": co_id, "target": f_id, "label": "OWNS"})
    for r in op_rows:
        if not isinstance(r, dict):
            continue
        f, v = r.get("f"), r.get("v")
        if not (f and v):
            continue
        f_id, v_id = f"f:{f}", f"v:{v}"
        nodes.setdefault(f_id, {"id": f_id, "type": "Fleet", "label": f})
        nodes.setdefault(v_id, {"id": v_id, "type": "Vessel", "label": v})
        edges.setdefault(f"{f_id}->{v_id}", {"id": f"{f_id}->{v_id}", "source": f_id, "target": v_id, "label": "OPERATES"})

    return {
        "dimension": "OrgMap",
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


def _scope_vessels(
    company: Optional[str], fleet: Optional[str], vessel: Optional[str] = None
) -> List[str]:
    """Resolve the set of vessels in scope from authored org data. A single vessel is
    the narrowest scope (the per-vessel role view), then fleet, then company, then all."""
    fov = org_data.fleet_of_vessel()
    cof = org_data.company_of_fleet()
    if vessel:
        return [vessel] if vessel in fov else []
    if fleet:
        return [v for v, f in fov.items() if f == fleet]
    if company:
        return [v for v, f in fov.items() if cof.get(f) == company]
    return org_data.vessels()


async def manning_gap(
    company: Optional[str] = None,
    fleet: Optional[str] = None,
    vessel: Optional[str] = None,
) -> Dict[str, Any]:
    """The headline OrgMap query: required vs. have headcount per rank for a scope
    (a single vessel, a fleet, a company's whole fleet, or everything).

    required = sum of (:Vessel)-[:REQUIRES_RANK {count}]->(:Rank) over scope vessels.
    have     = crew (:Crew)-[:ASSIGNED_TO]->(:Vessel) on scope vessels, by rank.
    gap      = required - have  (positive = short-staffed).
    """
    vessels = _scope_vessels(company, fleet, vessel)
    if not vessels:
        return {"scope": {"company": company, "fleet": fleet, "vessel": vessel, "vessels": []},
                "rows": [], "totals": {"required": 0, "have": 0, "gap": 0}}

    vlist = _in_list(vessels)

    req_rows = await run_cypher(
        f"MATCH (v:Vessel)-[req:REQUIRES_RANK]->(r:Rank) WHERE v.name IN {vlist} "
        f"RETURN {{rank: r.name, required: req.required}} AS v"
    )
    have_rows = await run_cypher(
        f"MATCH (c:Crew)-[:ASSIGNED_TO]->(v:Vessel) WHERE v.name IN {vlist} "
        f"RETURN {{rank: c.rank}} AS v"
    )

    required: Dict[str, int] = defaultdict(int)
    for r in req_rows:
        if isinstance(r, dict) and r.get("rank") is not None:
            required[r["rank"]] += int(r.get("required") or 0)
    have: Counter = Counter(
        r.get("rank") for r in have_rows if isinstance(r, dict) and r.get("rank")
    )

    ranks = sorted(set(required) | set(have))
    rows = [
        {"rank": rk, "required": required.get(rk, 0), "have": have.get(rk, 0),
         "gap": required.get(rk, 0) - have.get(rk, 0)}
        for rk in ranks
    ]
    # Most short-staffed first, then by rank for stability.
    rows.sort(key=lambda x: (-x["gap"], x["rank"]))

    return {
        "scope": {"company": company, "fleet": fleet, "vessel": vessel, "vessels": vessels},
        "rows": rows,
        "totals": {
            "required": sum(required.values()),
            "have": sum(have.values()),
            "gap": sum(required.values()) - sum(have.values()),
        },
    }
