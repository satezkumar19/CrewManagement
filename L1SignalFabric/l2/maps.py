"""In-memory L2 maps + a router that fans one unified ``L2Record`` to all of them.

The L2 backend (``entity_map.py`` / ``ops_map.py`` / ``org_map.py``) lives outside
this repo; these are lightweight, dependency-free stand-ins that realise the same
contracts in-process, so L1 can drive **every** map end-to-end from one record:

  * :class:`~l2.orgmap.OrgMap`   — tribal-knowledge node/edge graph (already here).
  * :class:`EntityMapStore`      — canonical Crew/Vessel/Port/Certificate/Contract
    graph, MERGE-d per crew/contract/vessel/port record (EntityMap contract §4).
  * :class:`OpsMapStore`         — per-case process-mining event log, event_type →
    activity, directly-follows (OpsMap contract §1/§3).

:class:`L2Router` takes an :class:`~l2.record.L2Record` and dispatches each facet
to its map — the same routing the wire endpoint ``POST /graph/records`` and the
in-process bus sink both use. Every store MERGEs on a business key, so at-least-once
redelivery is a no-op refresh.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from typing import Any, Optional

from l2.orgmap import OrgMap
from l2.record import EntityFacet, L2Record, MapTarget, OpsFacet, OrgFacet


# --------------------------------------------------------------------------- #
# Tiny property-graph backing store (shared by EntityMap)                     #
# --------------------------------------------------------------------------- #
class _Graph:
    """Bounded node/edge graph with idempotent upsert by id (Neo4j MERGE-alike)."""

    def __init__(self, max_nodes: int = 20000, max_edges: int = 80000) -> None:
        self.nodes: "OrderedDict[str, dict]" = OrderedDict()
        self.edges: "OrderedDict[str, dict]" = OrderedDict()
        self._max_nodes, self._max_edges = max_nodes, max_edges

    def node(self, nid: str, label: str, props: Optional[dict] = None,
             ts: Optional[str] = None) -> str:
        clean = {k: v for k, v in (props or {}).items() if v not in (None, "", "NULL")}
        n = self.nodes.get(nid)
        if n is None:
            self.nodes[nid] = {"id": nid, "label": label, "props": clean, "count": 1, "last_ts": ts}
            if len(self.nodes) > self._max_nodes:
                self.nodes.popitem(last=False)
        else:
            n["count"] += 1
            n["props"].update(clean)
            if ts:
                n["last_ts"] = ts
            self.nodes.move_to_end(nid)
        return nid

    def edge(self, src: str, dst: str, label: str, ts: Optional[str] = None) -> None:
        key = f"{src}|{label}|{dst}"
        e = self.edges.get(key)
        if e is None:
            self.edges[key] = {"id": key, "src": src, "dst": dst, "label": label,
                               "count": 1, "last_ts": ts}
            if len(self.edges) > self._max_edges:
                self.edges.popitem(last=False)
        else:
            e["count"] += 1
            if ts:
                e["last_ts"] = ts
            self.edges.move_to_end(key)

    def delete(self, nid: str) -> None:
        """Detach-delete a node and every edge touching it."""
        self.nodes.pop(nid, None)
        for k in [k for k, e in self.edges.items() if e["src"] == nid or e["dst"] == nid]:
            self.edges.pop(k, None)

    def stats(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes), "edges": len(self.edges),
            "by_node_label": dict(Counter(n["label"] for n in self.nodes.values())),
            "by_edge_label": dict(Counter(e["label"] for e in self.edges.values())),
        }

    def snapshot(self, limit_nodes: int = 400, limit_edges: int = 800) -> dict[str, Any]:
        nodes = list(self.nodes.values())[-limit_nodes:]
        ids = {n["id"] for n in nodes}
        edges = [e for e in self.edges.values() if e["src"] in ids and e["dst"] in ids][-limit_edges:]
        return {"nodes": nodes, "edges": edges, "stats": self.stats()}


# --------------------------------------------------------------------------- #
# EntityMap — the canonical crew graph (EntityMap contract §4)                 #
# --------------------------------------------------------------------------- #
_CREW_PROPS = ("crew_id", "name", "rank", "grade", "nationality",
               "port", "vessel", "status", "pool", "experience_years")


class EntityMapStore:
    """MERGE crew/contract/vessel/port records into the canonical entity graph."""

    def __init__(self) -> None:
        self.g = _Graph()
        self.merges = 0

    def merge(self, facet: EntityFacet, ts: Optional[str] = None) -> None:
        self.merges += 1
        ev, rec = facet.event, facet.record or {}
        if ev.startswith("crew."):
            self._merge_crew(ev, rec, ts)
        elif ev == "contract.upserted":
            self._merge_contract(rec, ts)
        elif ev == "vessel.upserted":
            name = rec.get("vessel") or rec.get("name")
            if name:
                self.g.node(f"Vessel:{name}", "Vessel",
                            {"name": name, **{k: rec.get(k) for k in ("imo_or_locode", "fleet", "country", "vessel_type", "status")}}, ts)
        elif ev == "port.upserted":
            name = rec.get("port") or rec.get("name")
            if name:
                self.g.node(f"Port:{name}", "Port",
                            {"name": name, **{k: rec.get(k) for k in ("imo_or_locode", "country", "status")}}, ts)

    def _merge_crew(self, ev: str, rec: dict, ts: Optional[str]) -> None:
        cid = rec.get("crew_id")
        if not cid:
            return
        if ev == "crew.deleted":
            self.g.delete(f"Crew:{cid}")
            self.g.delete(f"Contract:CT-{cid}")
            return
        crew = self.g.node(f"Crew:{cid}", "Crew", {k: rec.get(k) for k in _CREW_PROPS}, ts)
        for cert in rec.get("certifications") or []:
            c = self.g.node(f"Certificate:{cert}", "Certificate", {"type": cert}, ts)
            self.g.edge(crew, c, "HOLDS", ts)
        port = rec.get("port")
        p = None
        if port:
            p = self.g.node(f"Port:{port}", "Port", {"name": port}, ts)
            self.g.edge(crew, p, "CURRENTLY_AT", ts)
        vessel = rec.get("vessel")
        v = None
        if vessel and str(vessel).lower() != "available":
            v = self.g.node(f"Vessel:{vessel}", "Vessel", {"name": vessel}, ts)
            self.g.edge(crew, v, "ASSIGNED_TO", ts)
            if p:
                self.g.edge(v, p, "CALLS_AT", ts)
        # sign-off pool → engagement Contract (§4)
        if str(rec.get("pool", "")).lower() == "signoff" or ev == "crew.signed_off":
            ctid = f"CT-{cid}"
            ct = self.g.node(f"Contract:{ctid}", "Contract",
                             {"contract_id": ctid, "rank": rec.get("rank"), "vessel": vessel,
                              "port": port, "start_date": rec.get("joining_date"), "status": "Active"}, ts)
            self.g.edge(crew, ct, "SIGNED", ts)
            if v:
                self.g.edge(ct, v, "FOR_VESSEL", ts)
            if p:
                self.g.edge(ct, p, "AT_PORT", ts)

    def _merge_contract(self, rec: dict, ts: Optional[str]) -> None:
        ctid = rec.get("contract_id")
        if not ctid:
            return
        ct = self.g.node(f"Contract:{ctid}", "Contract",
                         {k: rec.get(k) for k in ("contract_id", "rank", "vessel", "port",
                                                  "start_date", "end_date", "status", "type")}, ts)
        if rec.get("crew_id"):
            self.g.edge(f"Crew:{rec['crew_id']}", ct, "SIGNED", ts)
        if rec.get("vessel"):
            self.g.edge(ct, f"Vessel:{rec['vessel']}", "FOR_VESSEL", ts)
        if rec.get("port"):
            self.g.edge(ct, f"Port:{rec['port']}", "AT_PORT", ts)

    def stats(self) -> dict[str, Any]:
        return {"merges": self.merges, **self.g.stats()}

    def snapshot(self, **kw) -> dict[str, Any]:
        return self.g.snapshot(**kw)


# --------------------------------------------------------------------------- #
# OpsMap — per-case process-mining log (OpsMap contract §1/§3)                 #
# --------------------------------------------------------------------------- #
_ACTIVITY = {
    "workflow_created": "Sign-Off Initiated",
    "crew_updated": "Sign-Off Confirmed",
    "auto_compliance": "Compliance Check",
    "sign_on_initiated": "Compliance Check",
    "crew_signed_on": "Signed On",
    "sign_on_rejected": "Sign-On Rejected",
    "workflow_failed": "Workflow Failed",
}
_AGENT_ACTIVITY = {
    "Crew Matching Agent": "Crew Matching",
    "Travel Agent": "Travel Arranged",
    "Notification Agent": "Crew Notified",
    "Compliance Agent": "Compliance Check",
}
_TERMINALS = {"Signed On", "Sign-On Rejected", "Workflow Failed"}


class OpsMapStore:
    """Group events by case, map to activities, mine the directly-follows graph."""

    def __init__(self) -> None:
        self.cases: "OrderedDict[str, list[dict]]" = OrderedDict()
        self.received = 0
        self.recorded = 0
        self.ignored = 0
        self.dfg: Counter = Counter()          # directly-follows edges across cases
        self.activities: Counter = Counter()

    def _activity(self, facet: OpsFacet) -> Optional[str]:
        if facet.event_type == "agent_completed":
            return _AGENT_ACTIVITY.get(facet.agent_name or "")
        return _ACTIVITY.get(facet.event_type)

    def record(self, facet: OpsFacet, ts: Optional[str] = None) -> bool:
        self.received += 1
        activity = self._activity(facet)
        if activity is None:                    # unrecognised/noise → ignored (contract §3)
            self.ignored += 1
            return False
        log = self.cases.setdefault(facet.case_id, [])
        # collapse immediate duplicate activities within a case (contract §2)
        if log and log[-1]["activity"] == activity:
            log[-1]["last_ts"] = ts or log[-1]["last_ts"]
            log[-1]["data"].update(facet.data or {})
            self.recorded += 1
            return True
        if log:
            self.dfg[(log[-1]["activity"], activity)] += 1
        log.append({"event_type": facet.event_type, "activity": activity,
                    "actor": facet.agent_name or "Master Agent",
                    "ts": ts, "last_ts": ts, "data": dict(facet.data or {}),
                    "terminal": activity in _TERMINALS})
        self.cases.move_to_end(facet.case_id)
        self.recorded += 1
        self.activities[activity] += 1
        return True

    def case_log(self, case_id: str) -> list[dict]:
        # L2 sorts each case by timestamp (contract §5)
        return sorted(self.cases.get(case_id, []), key=lambda s: s.get("ts") or "")

    def variants(self) -> Counter:
        v: Counter = Counter()
        for cid in self.cases:
            v[" → ".join(s["activity"] for s in self.case_log(cid))] += 1
        return v

    def stats(self) -> dict[str, Any]:
        return {
            "cases": len(self.cases),
            "received": self.received, "recorded": self.recorded, "ignored": self.ignored,
            "activities": dict(self.activities),
            "directly_follows": {f"{a} → {b}": n for (a, b), n in self.dfg.items()},
            "variants": dict(self.variants()),
        }

    def snapshot(self, limit: int = 50) -> dict[str, Any]:
        cases = list(self.cases)[-limit:]
        return {"stats": self.stats(),
                "cases": {cid: self.case_log(cid) for cid in cases}}


# --------------------------------------------------------------------------- #
# Router — one L2Record → every map                                           #
# --------------------------------------------------------------------------- #
class L2Router:
    """Dispatch each facet of an :class:`L2Record` to its map store."""

    def __init__(self, orgmap: Optional[OrgMap] = None) -> None:
        self.orgmap = orgmap or OrgMap()
        self.entitymap = EntityMapStore()
        self.opsmap = OpsMapStore()
        self.records = 0
        self.recent: deque[dict] = deque(maxlen=200)

    def route(self, rec: L2Record) -> dict[str, int]:
        """Route one record; returns per-map counts of facets applied."""
        self.records += 1
        ts = rec.occurred_at.isoformat() if rec.occurred_at else None
        applied = {"org": 0, "entity": 0, "ops": 0}
        for f in rec.facets:
            if isinstance(f, OrgFacet):
                self._route_org(rec, f, ts)
                applied["org"] += 1
            elif isinstance(f, EntityFacet):
                self.entitymap.merge(f, ts)
                applied["entity"] += 1
            elif isinstance(f, OpsFacet):
                self.opsmap.record(f, ts)
                applied["ops"] += 1
        self.recent.append({"record_id": rec.record_id, "source_system": rec.source_system,
                             "entity": rec.entity, "applied": applied})
        return applied

    def route_entity_ops(self, rec: L2Record, ts: Optional[str] = None) -> dict[str, int]:
        """Feed only EntityMap + OpsMap (OrgMap is driven by the legacy sink path)."""
        ts = ts or (rec.occurred_at.isoformat() if rec.occurred_at else None)
        applied = {"entity": 0, "ops": 0}
        for f in rec.facets:
            if isinstance(f, EntityFacet):
                self.entitymap.merge(f, ts)
                applied["entity"] += 1
            elif isinstance(f, OpsFacet):
                self.opsmap.record(f, ts)
                applied["ops"] += 1
        return applied

    def _route_org(self, rec: L2Record, f: OrgFacet, ts: Optional[str]) -> None:
        # rebuild the legacy projection dict OrgMap.upsert consumes from the envelope.
        self.orgmap.upsert({
            "kind": f.kind, "label": f.label, "props": f.props,
            "source_system": rec.source_system, "key": rec.key,
            "ts": ts, "id": f.node_id,
        })

    def stats(self) -> dict[str, Any]:
        return {
            "records_routed": self.records,
            "orgmap": self.orgmap.stats(),
            "entitymap": self.entitymap.stats(),
            "opsmap": self.opsmap.stats(),
        }
