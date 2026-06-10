"""OrgMap — in-memory operational knowledge graph with **upsert** semantics.

The append-only :class:`~l2.store.L2JsonlStore` records *every* projection as a
new line; the OrgMap is the real-graph counterpart: it merges those same L2
projection records into a **deduplicated node/edge graph**, keyed by entity
identity, so a person/channel/crew/vessel is one node no matter how many events
mention it (each re-mention just bumps a counter and refreshes ``last_ts``).

It consumes the exact dict returned by ``L2JsonlStore.project`` (kind = node /
edge / signoff_event), so wiring it in is "subscribe to the bus, write
downstream" — nothing upstream changes. It is dependency-light (stdlib only) and
swappable for a real graph DB (Neo4j ``MERGE``) behind the same ``upsert`` call.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from typing import Any, Optional

_EMAIL_SS = {"EMAIL", "GMAIL", "OUTLOOK"}


def _addr(value: Any) -> str:
    """Best-effort display string for an e-mail participant (dict or str)."""
    if isinstance(value, dict):
        return str(value.get("address") or value.get("name") or "unknown")
    return str(value) if value else "unknown"


class OrgMap:
    """A small, bounded property graph: ``nodes`` and ``edges`` upserted by id."""

    def __init__(self, max_nodes: int = 5000, max_edges: int = 20000) -> None:
        self.nodes: "OrderedDict[str, dict]" = OrderedDict()
        self.edges: "OrderedDict[str, dict]" = OrderedDict()
        self._max_nodes = max_nodes
        self._max_edges = max_edges
        self.upserts = 0

    # ----------------------------------------------------------------- upsert
    def _node(self, nid: str, label: str, props: Optional[dict] = None,
              ts: Optional[str] = None) -> str:
        clean = {k: v for k, v in (props or {}).items() if v not in (None, "")}
        node = self.nodes.get(nid)
        if node is None:
            self.nodes[nid] = {"id": nid, "label": label, "props": clean, "count": 1, "last_ts": ts}
            if len(self.nodes) > self._max_nodes:
                self.nodes.popitem(last=False)   # evict oldest (LRU)
        else:
            node["count"] += 1
            node["props"].update(clean)          # merge newest non-empty props
            if ts:
                node["last_ts"] = ts
            self.nodes.move_to_end(nid)
        return nid

    def _edge(self, src: str, dst: str, label: str, ts: Optional[str] = None,
              props: Optional[dict] = None) -> None:
        key = f"{src}|{label}|{dst}"
        edge = self.edges.get(key)
        if edge is None:
            self.edges[key] = {"id": key, "src": src, "dst": dst, "label": label,
                               "count": 1, "last_ts": ts,
                               "props": {k: v for k, v in (props or {}).items() if v not in (None, "")}}
            if len(self.edges) > self._max_edges:
                self.edges.popitem(last=False)
        else:
            edge["count"] += 1
            if ts:
                edge["last_ts"] = ts
            self.edges.move_to_end(key)

    def upsert(self, rec: Optional[dict]) -> None:
        """Merge one L2 projection record (node / edge / signoff_event) into the graph."""
        if not rec:
            return
        self.upserts += 1
        kind = rec.get("kind")
        ss = rec.get("source_system")
        label = rec.get("label")
        props = rec.get("props") or {}
        ts = rec.get("ts")

        if kind == "edge" and ss == "SLACK":
            user = props.get("user") or "unknown"
            chan_id = props.get("channel_id") or props.get("channel") or "unknown"
            chan = props.get("channel") or chan_id
            person = self._node(f"person:{user}", "Person", {"name": user}, ts)
            channel = self._node(f"channel:{chan_id}", "Channel", {"name": chan}, ts)
            self._edge(person, channel, label or "POSTED_IN", ts)
            self._crew_subgraph(props, ts, via=channel)

        elif kind == "edge" and ss in _EMAIL_SS:
            frm = _addr(props.get("from"))
            sender = self._node(f"person:{frm}", "Person", {"name": frm}, ts)
            tos = props.get("to") or []
            if isinstance(tos, (str, dict)):
                tos = [tos]
            for to in list(tos)[:5]:
                a = _addr(to)
                self._node(f"person:{a}", "Person", {"name": a}, ts)
                self._edge(sender, f"person:{a}", "EMAILED", ts, {"subject": props.get("subject")})
            self._crew_subgraph(props, ts, via=sender)

        elif kind == "signoff_event":
            sid = rec.get("id") or "signoff"
            self._node(sid, "SignOffEvent", {"subject": props.get("subject"), "from": props.get("from")}, ts)
            self._crew_subgraph(props, ts, signoff=sid)

        else:  # node — ERP / Notion / SharePoint / Database / … (upsert by natural key)
            key = rec.get("key") or {}
            ident = "-".join(str(v) for v in key.values()) or (rec.get("id") or "node")
            nid = f"{(label or ss or 'node').lower()}:{ident}"
            self._node(nid, label or (ss or "Node"), props, ts)

    def _crew_subgraph(self, props: dict, ts: Optional[str], *,
                       via: Optional[str] = None, signoff: Optional[str] = None) -> None:
        """Materialize the crew/vessel/port nodes parsed from a sign-on/off notice."""
        name = props.get("crew_member")
        if not name:
            return
        crew = self._node(f"crew:{props.get('crew_id') or name}", "Crew",
                          {"name": name, "role": props.get("role"),
                           "email": props.get("email"), "crew_id": props.get("crew_id")}, ts)
        if via:
            self._edge(via, crew, (props.get("action") or "mentions").upper(), ts)
        if signoff:
            self._edge(crew, signoff, "SIGN_OFF", ts)
        if props.get("vessel"):
            v = self._node(f"vessel:{props['vessel']}", "Vessel", {"name": props["vessel"]}, ts)
            self._edge(crew, v, "ON_VESSEL", ts)
        if props.get("port"):
            p = self._node(f"port:{props['port']}", "Port", {"name": props["port"]}, ts)
            self._edge(crew, p, "AT_PORT", ts)

    # ------------------------------------------------------------- read views
    def stats(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "upserts": self.upserts,
            "by_node_label": dict(Counter(n["label"] for n in self.nodes.values())),
            "by_edge_label": dict(Counter(e["label"] for e in self.edges.values())),
        }

    def snapshot(self, limit_nodes: int = 300, limit_edges: int = 600) -> dict[str, Any]:
        """Most-recent slice of the graph for the viewer (edges restricted to kept nodes)."""
        nodes = list(self.nodes.values())[-limit_nodes:]
        node_ids = {n["id"] for n in nodes}
        edges = [e for e in self.edges.values()
                 if e["src"] in node_ids and e["dst"] in node_ids][-limit_edges:]
        return {"nodes": nodes, "edges": edges, "stats": self.stats()}
