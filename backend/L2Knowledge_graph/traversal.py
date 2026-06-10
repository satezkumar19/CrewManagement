"""
L2 cross-dimension TRAVERSAL — one connected graph for a single OpsMap process case.

Stitches the three L2 dimensions into a single diagram so a crew-change case can be
read end-to-end as one connection rather than three separate maps:

    PROCESS (OpsMap)        ENTITIES (EntityMap)        ORG (OrgMap)
    the case's activity     the sign-off crew, the      the vessel's fleet and
    path = the active line  vessel, the replacement     company (+ sign-off rank)

The bridge is the **Vessel**: EntityMap and OrgMap both key it as ``v:<name>``, so the
same vessel is ONE shared node joining the entity and org zones. The process spine
(consecutive activities) plus the process→crew bridge edges form the highlighted
"active line". Nodes carry a ``zone`` and a ``col`` so the UI lays them out in three
left→right bands.

Reuses entity_map.case_subgraph (entities), org_map.org_structure (hierarchy) and
ops_map.process_cases (the case + its path) — no new graph queries of its own beyond
those, so it stays consistent with each dimension's own view.
"""
from typing import Any, Dict, Optional

from L2Knowledge_graph import entity_map as _entity_map
from L2Knowledge_graph import ops_map as _ops_map
from L2Knowledge_graph import org_map as _org_map

# Activities that mean the process reached "replacement finding" — only then is the
# sign-on candidate part of the path (mirrors the rule used in the graph UI).
_MATCH_REACHED = {"Crew Matching", "Compliance Check", "Signed On", "Sign-On Rejected"}


async def case_traversal(case_id: str) -> Optional[Dict[str, Any]]:
    """Assemble the unified traversal graph for one mined case, or None if no such case."""
    cases = _ops_map.process_cases().get("cases", [])
    case = next((c for c in cases if c.get("case_id") == case_id), None)
    if case is None:
        return None

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    active_ids: set = set()

    def add_node(nid: str, ntype: str, label: str, zone: str, col: int,
                 sub: str = "", active: bool = False) -> None:
        n = nodes.get(nid)
        if n is None:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, "zone": zone,
                          "col": col, "sub": sub, "active": active}
        elif active:
            nodes[nid]["active"] = True
        if active:
            active_ids.add(nid)

    def add_edge(src: str, tgt: str, label: str = "", active: bool = False) -> None:
        eid = f"{src}=>{tgt}"
        if eid not in edges:
            edges[eid] = {"id": eid, "source": src, "target": tgt, "label": label, "active": active}
        elif active:
            edges[eid]["active"] = True

    sign_off = case.get("sign_off_crew")
    sign_off_rank = case.get("sign_off_rank")
    vessel = case.get("sign_off_vessel")
    path = case.get("path") or []
    reached_match = any(a in _MATCH_REACHED for a in path)
    candidate = case.get("sign_on_crew") if reached_match else None

    # ── PROCESS zone (col 0) — the activity path IS the active line ──────────────────
    prev: Optional[str] = None
    for act in path:
        pid = f"p:{act}"
        add_node(pid, "Activity", act, "process", 0, active=True)
        if prev:
            add_edge(prev, pid, "", active=True)
        prev = pid

    # ── ENTITY zone — reuse case_subgraph, re-key into the unified id space ──────────
    # Crew → crew:<name> (col 1), Vessel → v:<name> (col 2, the bridge), the rest → e:<id> (col 3).
    ent = await _entity_map.case_subgraph(crew=sign_off, vessel=vessel, candidate=candidate)
    ent_active = set(ent.get("active_ids", []))
    idmap: Dict[str, str] = {}
    for n in ent.get("nodes", []):
        if n["type"] == "Crew":
            nid = f"crew:{n['label']}"
            add_node(nid, "Crew", n["label"], "entity", 1, sub=n.get("sub", ""),
                     active=n["id"] in ent_active)
        elif n["type"] == "Vessel":
            nid = f"v:{n['label']}"   # canonical — shared with the OrgMap zone
            add_node(nid, "Vessel", n["label"], "entity", 2, active=True)
        else:
            nid = f"e:{n['id']}"
            add_node(nid, n["type"], n["label"], "entity", 3, sub=n.get("sub", ""))
        idmap[n["id"]] = nid
    for e in ent.get("edges", []):
        s, t = idmap.get(e["source"]), idmap.get(e["target"])
        if s and t:
            add_edge(s, t, e.get("label", ""))

    # ── ORG zone — walk vessel → fleet → company off the shared vessel node ─────────
    if vessel:
        vId = f"v:{vessel}"
        struct = await _org_map.org_structure()
        s_nodes = {n["id"]: n for n in struct.get("nodes", [])}
        fleet_id = next((e["source"] for e in struct.get("edges", [])
                         if e.get("label") == "OPERATES" and e.get("target") == vId), None)
        if fleet_id:
            add_node(fleet_id, "Fleet", s_nodes.get(fleet_id, {}).get("label", fleet_id),
                     "org", 4, active=True)
            add_edge(fleet_id, vId, "OPERATES")
            company_id = next((e["source"] for e in struct.get("edges", [])
                               if e.get("label") == "OWNS" and e.get("target") == fleet_id), None)
            if company_id:
                add_node(company_id, "Company", s_nodes.get(company_id, {}).get("label", company_id),
                         "org", 5, active=True)
                add_edge(company_id, fleet_id, "OWNS")

    # Sign-off rank (org concept) hung off the sign-off crew.
    if sign_off_rank and sign_off and f"crew:{sign_off}" in nodes:
        rid = f"r:{sign_off_rank}"
        add_node(rid, "Rank", sign_off_rank, "org", 6)
        add_edge(f"crew:{sign_off}", rid, "HAS_RANK")

    # ── BRIDGE edges — the active line crossing from process into the entities ──────
    so_act = "p:Sign-Off Initiated"
    if so_act in nodes and sign_off and f"crew:{sign_off}" in nodes:
        add_edge(so_act, f"crew:{sign_off}", "signs off", active=True)
    if candidate and f"crew:{candidate}" in nodes:
        match_act = next((f"p:{a}" for a in ("Crew Matching", "Compliance Check", "Signed On")
                          if f"p:{a}" in nodes), None)
        if match_act:
            add_edge(match_act, f"crew:{candidate}", "replacement", active=True)

    return {
        "case": {k: case.get(k) for k in
                 ("case_id", "sign_off_crew", "sign_off_rank", "sign_off_vessel",
                  "sign_on_crew", "outcome")},
        "candidate_reached": reached_match,
        "zones": ["process", "entity", "org"],
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "active_ids": sorted(active_ids),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }
