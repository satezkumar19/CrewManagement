"""
L2 Knowledge Graph — Dimension 1: EntityMap.

The EntityMap is the *canonical entity layer* of the L2 context graph: the single
source of truth for the real-world things the maritime crew domain is made of and
the factual relationships between them. It is built inside the same PostgreSQL
instance as the relational `crew` table, using Apache AGE (see database.graph_db,
which owns the only Cypher connection and the `maritime` graph).

Five entity types (per the L2 plan)::

    (:Crew)        — a seafarer (candidate or onboard); keyed by crew_id
    (:Vessel)      — a ship; keyed by name
    (:Port)        — a port of call / current location; keyed by name
    (:Certificate) — a certificate / qualification type; keyed by type
    (:Contract)    — an engagement of one crew on one vessel; keyed by contract_id

Factual relationships (the EntityMap edges)::

    (:Crew)-[:HOLDS]->(:Certificate)        crew qualifications
    (:Crew)-[:ASSIGNED_TO]->(:Vessel)       current ship (onboard crew)
    (:Crew)-[:CURRENTLY_AT]->(:Port)        where the crew physically is
    (:Vessel)-[:CALLS_AT]->(:Port)          ship's port of call
    (:Crew)-[:SIGNED]->(:Contract)          the engagement the crew signed
    (:Contract)-[:FOR_VESSEL]->(:Vessel)    contract's ship
    (:Contract)-[:AT_PORT]->(:Port)         contract's joining port

DESIGN NOTE — "no data duplication across dimensions": EntityMap owns the node
identities (Crew/Vessel/Port/Certificate). The other two L2 dimensions reuse the
SAME nodes and only ADD edges:
  * OpsMap  (planned) overlays the sign-off → search → match → onboard process as
    state/edge annotations on the existing Crew nodes — it never re-creates them.
  * OrgMap  (planned) adds Company/Fleet nodes and OPERATES/BELONGS_TO edges that
    point at the existing Vessel nodes.
Because every dimension MERGEs on the same business keys, a Vessel called
"MV Pacific Star" is one node no matter how many dimensions touch it.

All node creation goes through MERGE on the business key, so re-running
build_entity_map() is idempotent (it refreshes properties, never duplicates).
"""
from collections import Counter
from typing import Any, Dict, List, Optional

import structlog

from database.crew_repository import get_sign_off_crew, get_sign_on_crew
from L2Knowledge_graph.graph_db import age_enabled, ensure_graph, run_cypher

log = structlog.get_logger()

# Node labels and edge types — exported so the API / tests / docs share one vocabulary.
ENTITY_LABELS = ["Crew", "Vessel", "Port", "Certificate", "Contract"]
ENTITY_EDGES = [
    "HOLDS",
    "ASSIGNED_TO",
    "CURRENTLY_AT",
    "CALLS_AT",
    "SIGNED",
    "FOR_VESSEL",
    "AT_PORT",
]

# A vessel value of "Available" in the crew table is a placeholder for sign-on
# candidates who are not yet assigned a ship — it is not a real Vessel entity.
_UNASSIGNED_VESSEL = "Available"


def _q(value: Any) -> str:
    """Escape a string for safe inlining as a Cypher single-quoted literal.

    AGE runs Cypher inside a SQL function and has no client-side parameter binding,
    so literals are inlined (same approach as scripts/seed_graph.py). Every value
    that reaches a query — including API search filters — is passed through here.
    """
    return str(value if value is not None else "").replace("\\", "\\\\").replace("'", "\\'")


# ── Build / seed ────────────────────────────────────────────────────────────────


async def _merge_crew(c: Dict[str, Any], pool: str) -> None:
    cid = _q(c.get("crew_id"))
    await run_cypher(
        f"MERGE (c:Crew {{crew_id:'{cid}'}}) "
        f"SET c.name='{_q(c.get('name'))}', c.rank='{_q(c.get('rank'))}', "
        f"c.grade='{_q(c.get('grade'))}', c.nationality='{_q(c.get('nationality'))}', "
        f"c.port='{_q(c.get('port'))}', c.vessel='{_q(c.get('vessel'))}', "
        f"c.status='{_q(c.get('status'))}', c.pool='{_q(pool)}', "
        f"c.experience_years={int(c.get('experience_years') or 0)}"
    )

    # Certificates the crew holds.
    for cert in (c.get("certifications") or []):
        await run_cypher(
            f"MERGE (t:Certificate {{type:'{_q(cert)}'}}) "
            f"WITH t MATCH (c:Crew {{crew_id:'{cid}'}}) MERGE (c)-[:HOLDS]->(t)"
        )

    # Current port (where the seafarer physically is).
    if c.get("port"):
        await run_cypher(
            f"MERGE (p:Port {{name:'{_q(c['port'])}'}}) "
            f"WITH p MATCH (c:Crew {{crew_id:'{cid}'}}) MERGE (c)-[:CURRENTLY_AT]->(p)"
        )

    # Assigned vessel + the vessel's port of call (onboard crew only).
    vessel = c.get("vessel")
    if vessel and vessel != _UNASSIGNED_VESSEL:
        await run_cypher(
            f"MERGE (v:Vessel {{name:'{_q(vessel)}'}}) "
            f"WITH v MATCH (c:Crew {{crew_id:'{cid}'}}) MERGE (c)-[:ASSIGNED_TO]->(v)"
        )
        if c.get("port"):
            await run_cypher(
                f"MATCH (v:Vessel {{name:'{_q(vessel)}'}}), (p:Port {{name:'{_q(c['port'])}'}}) "
                f"MERGE (v)-[:CALLS_AT]->(p)"
            )


async def _merge_contract(c: Dict[str, Any]) -> None:
    """Onboard (sign-off pool) crew have an active engagement — model it as a Contract
    entity linking the crew to their vessel and joining port."""
    vessel = c.get("vessel")
    if not vessel or vessel == _UNASSIGNED_VESSEL:
        return
    cid = _q(c.get("crew_id"))
    contract_id = f"CT-{c.get('crew_id')}"
    await run_cypher(
        f"MERGE (k:Contract {{contract_id:'{_q(contract_id)}'}}) "
        f"SET k.rank='{_q(c.get('rank'))}', k.vessel='{_q(vessel)}', "
        f"k.port='{_q(c.get('port'))}', k.start_date='{_q(c.get('joining_date'))}', "
        f"k.status='Active'"
    )
    await run_cypher(
        f"MATCH (c:Crew {{crew_id:'{cid}'}}), (k:Contract {{contract_id:'{_q(contract_id)}'}}) "
        f"MERGE (c)-[:SIGNED]->(k)"
    )
    await run_cypher(
        f"MATCH (k:Contract {{contract_id:'{_q(contract_id)}'}}), (v:Vessel {{name:'{_q(vessel)}'}}) "
        f"MERGE (k)-[:FOR_VESSEL]->(v)"
    )
    if c.get("port"):
        await run_cypher(
            f"MATCH (k:Contract {{contract_id:'{_q(contract_id)}'}}), (p:Port {{name:'{_q(c['port'])}'}}) "
            f"MERGE (k)-[:AT_PORT]->(p)"
        )


async def build_entity_map() -> Dict[str, int]:
    """(Re)build the EntityMap dimension in AGE from the crew table. Idempotent.

    Returns the post-build summary (node/edge counts). Requires GRAPH_BACKEND=age;
    raises RuntimeError otherwise so the seed script fails loudly instead of silently
    doing nothing.
    """
    if not age_enabled():
        raise RuntimeError(
            "EntityMap requires the AGE backend. Set GRAPH_BACKEND=age and point "
            "DATABASE_URL at an AGE-enabled Postgres, then re-run."
        )
    await ensure_graph()

    sign_on = await get_sign_on_crew()
    sign_off = await get_sign_off_crew()

    for c in sign_on:
        await _merge_crew(c, pool="signon")
    for c in sign_off:
        await _merge_crew(c, pool="signoff")
        await _merge_contract(c)

    summary = await entity_map_summary()
    log.info("entity_map.built", crew=len(sign_on) + len(sign_off), **summary["nodes"])
    return summary


# ── Query interface ─────────────────────────────────────────────────────────────


async def entity_map_summary() -> Dict[str, Any]:
    """Per-label node counts and per-type edge counts. Used to confirm the
    EntityMap is fully populated (exit criterion: 'all dimensions populated')."""
    node_rows = await run_cypher("MATCH (n) RETURN {label: labels(n)[0]} AS v")
    edge_rows = await run_cypher("MATCH ()-[r]->() RETURN {t: type(r)} AS v")
    nodes = Counter(r.get("label") for r in node_rows if isinstance(r, dict))
    edges = Counter(r.get("t") for r in edge_rows if isinstance(r, dict))
    return {
        "nodes": {label: nodes.get(label, 0) for label in ENTITY_LABELS},
        "edges": {etype: edges.get(etype, 0) for etype in ENTITY_EDGES},
        "total_nodes": sum(nodes.values()),
        "total_edges": sum(edges.values()),
    }


async def search_crew(
    rank: Optional[str] = None,
    certificate: Optional[str] = None,
    port: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Crew search by rank + certificate + port (any subset) — the headline
    multi-relationship query. rank is a Crew property; certificate and port are
    matched by traversing HOLDS / CURRENTLY_AT edges, so this exercises the graph
    rather than a flat WHERE.
    """
    matches = ["MATCH (c:Crew)"]
    wheres: List[str] = []
    if certificate:
        matches.append(f"MATCH (c)-[:HOLDS]->(:Certificate {{type:'{_q(certificate)}'}})")
    if port:
        matches.append(f"MATCH (c)-[:CURRENTLY_AT]->(:Port {{name:'{_q(port)}'}})")
    if rank:
        wheres.append(f"c.rank = '{_q(rank)}'")

    query = " ".join(matches)
    if wheres:
        query += " WHERE " + " AND ".join(wheres)
    query += (
        " RETURN {crew_id:c.crew_id, name:c.name, rank:c.rank, grade:c.grade, "
        "nationality:c.nationality, port:c.port, vessel:c.vessel, status:c.status, "
        "pool:c.pool, experience_years:c.experience_years} AS v"
    )
    rows = await run_cypher(query)
    rows = [r for r in rows if isinstance(r, dict) and r.get("crew_id")]
    rows.sort(key=lambda r: r.get("crew_id") or "")
    return rows[:limit]


async def facets() -> Dict[str, List[str]]:
    """Distinct filter values for the query UI: every rank, certificate and port
    actually present in the graph. Sorted so the dropdowns are stable."""
    rank_rows = await run_cypher("MATCH (c:Crew) RETURN {v: c.rank} AS v")
    cert_rows = await run_cypher("MATCH (t:Certificate) RETURN {v: t.type} AS v")
    port_rows = await run_cypher("MATCH (p:Port) RETURN {v: p.name} AS v")

    def _vals(rows):
        return sorted({r["v"] for r in rows if isinstance(r, dict) and r.get("v")})

    return {"ranks": _vals(rank_rows), "certificates": _vals(cert_rows), "ports": _vals(port_rows)}


async def search_subgraph(
    rank: Optional[str] = None,
    certificate: Optional[str] = None,
    port: Optional[str] = None,
    limit: int = 12,
) -> Dict[str, Any]:
    """Return a React-Flow-ready subgraph (nodes + edges) for a crew search: the
    matched crew plus everything they directly connect to (certificates, port,
    vessel, contract). This is what the Standalone Query UI renders.
    """
    matches = ["MATCH (c:Crew)"]
    wheres: List[str] = []
    if certificate:
        matches.append(f"MATCH (c)-[:HOLDS]->(:Certificate {{type:'{_q(certificate)}'}})")
    if port:
        matches.append(f"MATCH (c)-[:CURRENTLY_AT]->(:Port {{name:'{_q(port)}'}})")
    if rank:
        wheres.append(f"c.rank = '{_q(rank)}'")

    query = " ".join(matches)
    if wheres:
        query += " WHERE " + " AND ".join(wheres)
    # Cap the crew set BEFORE fanning out to neighbours so the picture stays legible.
    query += f" WITH c ORDER BY c.crew_id LIMIT {int(limit)} OPTIONAL MATCH (c)-[r]->(n) "
    query += (
        "RETURN {cid: id(c), cname: c.name, crank: c.rank, cpool: c.pool, "
        "rel: type(r), nid: id(n), "
        "nlabel: coalesce(n.name, n.type, n.contract_id), ntype: labels(n)[0]} AS v"
    )
    rows = await run_cypher(query)

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    crew_ids = set()
    for r in rows:
        if not isinstance(r, dict) or r.get("cid") is None:
            continue
        cnode = f"c{r['cid']}"
        crew_ids.add(cnode)
        nodes.setdefault(cnode, {
            "id": cnode, "type": "Crew", "label": r.get("cname") or "",
            "sub": r.get("crank") or "", "pool": r.get("cpool") or "",
        })
        if r.get("rel") and r.get("nid") is not None:
            tnode = f"n{r['nid']}"
            nodes.setdefault(tnode, {
                "id": tnode, "type": r.get("ntype") or "?",
                "label": str(r.get("nlabel") or ""), "sub": r.get("ntype") or "",
            })
            eid = f"{cnode}-{r['rel']}-{tnode}"
            edges.setdefault(eid, {
                "id": eid, "source": cnode, "target": tnode, "label": r["rel"],
            })

    return {
        "filters": {"rank": rank, "certificate": certificate, "port": port},
        "crew_count": len(crew_ids),
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


async def case_subgraph(
    crew: Optional[str] = None,
    vessel: Optional[str] = None,
    candidate: Optional[str] = None,
) -> Dict[str, Any]:
    """A focused subgraph for one OpsMap process case: the sign-off crew, the
    replacement candidate, and their shared vessel — each with its direct neighbours
    (certificate / port / contract). The focal entities actually in the case are
    returned in ``active_ids`` so the UI can highlight the live process path,
    distinct from an ordinary click selection. Crew/vessel are matched by name (the
    fields an OpsMap case carries)."""
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    active: set = set()

    async def _add_crew(name: str) -> None:
        rows = await run_cypher(
            f"MATCH (c:Crew {{name:'{_q(name)}'}}) OPTIONAL MATCH (c)-[r]->(n) "
            "RETURN {cid: id(c), cname: c.name, crank: c.rank, cpool: c.pool, "
            "rel: type(r), nid: id(n), "
            "nlabel: coalesce(n.name, n.type, n.contract_id), ntype: labels(n)[0]} AS v"
        )
        for r in rows:
            if not isinstance(r, dict) or r.get("cid") is None:
                continue
            cnode = f"c{r['cid']}"
            nodes.setdefault(cnode, {
                "id": cnode, "type": "Crew", "label": r.get("cname") or "",
                "sub": r.get("crank") or "", "pool": r.get("cpool") or "",
            })
            active.add(cnode)
            # Only EntityMap relationships — skip OrgMap overlay edges (HAS_RANK etc.)
            # so the case view stays an entity picture, not a rank fan-out.
            if r.get("rel") in ENTITY_EDGES and r.get("nid") is not None:
                tnode = f"n{r['nid']}"
                nodes.setdefault(tnode, {
                    "id": tnode, "type": r.get("ntype") or "?",
                    "label": str(r.get("nlabel") or ""), "sub": r.get("ntype") or "",
                })
                eid = f"{cnode}-{r['rel']}-{tnode}"
                edges.setdefault(eid, {"id": eid, "source": cnode, "target": tnode, "label": r["rel"]})

    if crew:
        await _add_crew(crew)
    if candidate:
        await _add_crew(candidate)

    # The case's vessel + its port of call. The vessel node id (n<id(v)>) is the same
    # one the sign-off crew's ASSIGNED_TO edge points at, so they merge into one node.
    if vessel:
        vrows = await run_cypher(
            f"MATCH (v:Vessel {{name:'{_q(vessel)}'}}) OPTIONAL MATCH (v)-[r]->(n) "
            "RETURN {vid: id(v), vname: v.name, rel: type(r), nid: id(n), "
            "nlabel: coalesce(n.name, n.type), ntype: labels(n)[0]} AS v"
        )
        for r in vrows:
            if not isinstance(r, dict) or r.get("vid") is None:
                continue
            vnode = f"n{r['vid']}"
            nodes.setdefault(vnode, {
                "id": vnode, "type": "Vessel", "label": r.get("vname") or "", "sub": "Vessel",
            })
            active.add(vnode)
            if r.get("rel") in ENTITY_EDGES and r.get("nid") is not None:
                tnode = f"n{r['nid']}"
                nodes.setdefault(tnode, {
                    "id": tnode, "type": r.get("ntype") or "?",
                    "label": str(r.get("nlabel") or ""), "sub": r.get("ntype") or "",
                })
                eid = f"{vnode}-{r['rel']}-{tnode}"
                edges.setdefault(eid, {"id": eid, "source": vnode, "target": tnode, "label": r["rel"]})

    return {
        "case": {"crew": crew, "vessel": vessel, "candidate": candidate},
        "crew_count": sum(1 for n in nodes.values() if n.get("type") == "Crew"),
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "active_ids": sorted(active),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


async def node_detail(node_id: str) -> Optional[Dict[str, Any]]:
    """Full detail for a single node, addressed by the id used in the subgraph
    payload (e.g. 'c105...' for crew, 'n106...' for everything else). Returns the
    node's label, all its properties, and its incoming + outgoing relationships —
    everything the UI needs to show when a node is clicked.
    """
    digits = "".join(ch for ch in str(node_id) if ch.isdigit())
    if not digits:
        return None
    info = await run_cypher(
        f"MATCH (n) WHERE id(n) = {digits} "
        "RETURN {label: labels(n)[0], props: properties(n)} AS v"
    )
    info = [r for r in info if isinstance(r, dict) and r.get("label")]
    if not info:
        return None
    out = await run_cypher(
        f"MATCH (n)-[r]->(m) WHERE id(n) = {digits} "
        "RETURN {dir:'out', rel: type(r), other_id: id(m), "
        "other: coalesce(m.name, m.type, m.contract_id, m.crew_id), "
        "other_type: labels(m)[0]} AS v"
    )
    inc = await run_cypher(
        f"MATCH (n)<-[r]-(m) WHERE id(n) = {digits} "
        "RETURN {dir:'in', rel: type(r), other_id: id(m), "
        "other: coalesce(m.name, m.type, m.contract_id, m.crew_id), "
        "other_type: labels(m)[0]} AS v"
    )
    rels = [r for r in (out + inc) if isinstance(r, dict) and r.get("rel")]
    # AGE node ids are 60-bit integers that exceed JS's safe integer range, so
    # serialise other_id as a STRING — otherwise the browser's JSON.parse rounds it
    # and a relationship-row "jump" would query the wrong node.
    for r in rels:
        if r.get("other_id") is not None:
            r["other_id"] = str(r["other_id"])
    return {
        "id": str(node_id),
        "label": info[0].get("label"),
        "properties": info[0].get("props") or {},
        "relationships": rels,
        "degree": len(rels),
    }


async def traverse_crew(crew_id: str, max_hops: int = 2) -> Dict[str, Any]:
    """Full relationship traversal for one crew member — every entity reachable
    within `max_hops`, with the path that reaches it. Demonstrates multi-hop
    traversal such as Crew → Vessel → Port and Crew → Contract → Vessel.
    """
    cid = _q(crew_id)
    direct = await run_cypher(
        f"MATCH (c:Crew {{crew_id:'{cid}'}})-[r]->(n) "
        "RETURN {relationship: type(r), target_type: labels(n)[0], "
        "target: coalesce(n.name, n.type, n.contract_id, n.crew_id)} AS v"
    )
    paths = await run_cypher(
        f"MATCH path = (c:Crew {{crew_id:'{cid}'}})-[*1..{int(max_hops)}]->(n) "
        "RETURN {hops: length(path), endpoint: coalesce(n.name, n.type, n.contract_id, n.crew_id), "
        "endpoint_type: labels(n)[0]} AS v"
    )
    direct = [r for r in direct if isinstance(r, dict) and r.get("relationship")]
    paths = [r for r in paths if isinstance(r, dict) and r.get("endpoint")]
    return {
        "crew_id": crew_id,
        "direct_relationships": direct,
        "reachable_within_hops": paths,
        "neighbour_count": len({(r["relationship"], r["target"]) for r in direct}),
    }
