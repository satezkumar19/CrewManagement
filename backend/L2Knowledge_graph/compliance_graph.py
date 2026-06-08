"""
Compliance rules as graph DATA + the subgraph builder.

This is the "context graph" made concrete for the compliance domain. Two things
live here:

1. The compliance rules that used to be hardcoded inside compliance_agent.py
   (PORT_RESTRICTIONS, the rank -> required-certificate mapping). Keeping them in
   one module makes them editable data rather than scattered if/else logic, and is
   the single source of truth the agent imports.

2. build_compliance_subgraph(): turns a seafarer + boarding port into a small
   graph (nodes + typed edges) describing the neighbourhood a compliance decision
   depends on -- Seafarer -> nationality/vessel/certs, Vessel -> Port, Port ->
   restricted nationalities -- and marks which nodes/edges are OK, a WARNING, or a
   BLOCK. The frontend renders exactly this structure (React Flow), so the graph
   the agent reasons over is the same graph the user sees.

The builder is pure Python and needs no database, so the feature works under the
default "fallback" backend. When AGE is enabled the same shape can be produced from
Cypher (see database.graph_db); callers don't care which path ran.
"""
from datetime import date
from typing import Any, Dict, List, Optional

import structlog

log = structlog.get_logger()

# ── Rules as data (moved out of compliance_agent.py) ───────────────────────────

# Port -> restricted nationalities + minimum medical-certificate validity (days).
PORT_RESTRICTIONS: Dict[str, Dict[str, Any]] = {
    "Singapore": {"visa_required": ["Iranian", "North Korean"], "min_medical_days": 30},
    "Rotterdam": {"visa_required": ["Iranian"], "min_medical_days": 60},
    "Houston": {"visa_required": ["Cuban", "Iranian", "North Korean"], "min_medical_days": 30},
    "Dubai": {"visa_required": [], "min_medical_days": 30},
    "Shanghai": {"visa_required": [], "min_medical_days": 30},
    "Manila": {"visa_required": [], "min_medical_days": 30},
    "Mumbai": {"visa_required": [], "min_medical_days": 30},
    "Piraeus": {"visa_required": [], "min_medical_days": 30},
}

# Certificates every seafarer needs, plus the extra ones officers/engineers need.
BASE_REQUIRED_CERTS = ["STCW Basic Safety", "Proficiency in Survival Craft"]
OFFICER_REQUIRED_CERTS = ["Advanced Fire Fighting", "Medical First Aid"]

# Severity ranking so we can compute the worst status across the graph.
_SEVERITY = {"ok": 0, "warn": 1, "block": 2}
_STATUS_TO_VERDICT = {"ok": "passed", "warn": "warning", "block": "failed"}


def required_certs_for_rank(rank: Optional[str]) -> List[str]:
    """Required certificates for a rank (officers/masters/engineers need more)."""
    req = list(BASE_REQUIRED_CERTS)
    r = rank or ""
    if any(k in r for k in ("Officer", "Master", "Engineer")):
        req += OFFICER_REQUIRED_CERTS
    return req


def _days_until(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - date.today()).days
    except Exception:
        return None


def _worst(statuses: List[str]) -> str:
    worst = "ok"
    for s in statuses:
        if _SEVERITY.get(s, 0) > _SEVERITY[worst]:
            worst = s
    return worst


def build_compliance_subgraph(
    crew: Dict[str, Any],
    port: str,
    backend: str = "fallback",
    port_restrictions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the compliance context subgraph for one seafarer + boarding port.

    Returns a dict: {nodes, edges, findings, verdict, backend}. Node/edge `status`
    is one of "ok" | "warn" | "block". Node x/y are layout hints for the UI.

    `port_restrictions` lets the caller inject the rules from an external source
    (e.g. fetched from AGE via the maritime graph) instead of looking them up in
    the local PORT_RESTRICTIONS dict. Same shape: {visa_required, min_medical_days}.
    """
    crew = crew or {}
    crew_id = str(crew.get("crew_id") or "unknown")
    name = crew.get("name") or "Incoming crew"
    rank = crew.get("rank") or ""
    nationality = crew.get("nationality") or "Unknown"
    vessel = crew.get("vessel") or "Assigned vessel"
    held_certs = set(crew.get("certifications") or [])

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    findings: List[str] = []

    seafarer_id = f"seafarer:{crew_id}"
    country_id = f"country:{nationality}"
    vessel_id = f"vessel:{vessel}"
    port_id = f"port:{port}"

    # Anchor nodes
    nodes.append({"id": seafarer_id, "type": "Seafarer", "label": name, "sub": rank, "status": "ok", "x": 40, "y": 200})
    nodes.append({"id": country_id, "type": "Country", "label": nationality, "sub": "nationality", "status": "ok", "x": 280, "y": 40})
    nodes.append({"id": vessel_id, "type": "Vessel", "label": vessel, "sub": "assigned", "status": "ok", "x": 280, "y": 200})

    restrictions = port_restrictions if port_restrictions is not None else PORT_RESTRICTIONS.get(port, {})
    min_medical_days = restrictions.get("min_medical_days", 30)
    nodes.append({"id": port_id, "type": "Port", "label": port, "sub": f"min medical {min_medical_days}d", "status": "ok", "x": 280, "y": 360})

    edges.append({"id": "e_nat", "source": seafarer_id, "target": country_id, "label": "NATIONAL_OF", "status": "ok"})
    edges.append({"id": "e_asg", "source": seafarer_id, "target": vessel_id, "label": "ASSIGNED_TO", "status": "ok"})
    edges.append({"id": "e_call", "source": vessel_id, "target": port_id, "label": "CALLS_AT", "status": "ok"})

    # ── Port nationality restriction (the headline multi-hop check) ────────────
    visa_status = crew.get("visa_status", "Unknown")
    restricted = restrictions.get("visa_required", [])
    if nationality in restricted and visa_status != "Valid":
        edges.append({"id": "e_restrict", "source": port_id, "target": country_id, "label": "RESTRICTS", "status": "block"})
        nodes[1]["status"] = "block"  # country
        nodes[3]["status"] = "block"  # port
        findings.append(f"{nationality} nationals require a valid visa at {port} — visa is '{visa_status}'.")
    elif nationality in restricted:
        edges.append({"id": "e_restrict", "source": port_id, "target": country_id, "label": "RESTRICTS (cleared)", "status": "warn"})
        findings.append(f"{nationality} is visa-restricted at {port}, but a valid visa is on file.")
    else:
        findings.append(f"No nationality restriction for {nationality} at {port}.")

    # ── Document / certificate nodes hanging off the seafarer ──────────────────
    cert_x = 540
    row = 0

    def add_cert(label: str, sub: str, status: str, edge_label: str = "HOLDS") -> None:
        nonlocal row
        cid = f"cert:{label}"
        nodes.append({"id": cid, "type": "Certificate", "label": label, "sub": sub, "status": status, "x": cert_x, "y": 20 + row * 78})
        edges.append({"id": f"e_{label}", "source": seafarer_id, "target": cid, "label": edge_label, "status": status})
        row += 1

    # Passport
    pd = _days_until(crew.get("passport_expiry"))
    if pd is None:
        add_cert("Passport", "no date", "block"); findings.append("Passport expiry not provided.")
    elif pd < 0:
        add_cert("Passport", "expired", "block"); findings.append("Passport has expired.")
    elif pd < 180:
        add_cert("Passport", f"{pd}d left", "warn"); findings.append(f"Passport expires in {pd} days.")
    else:
        add_cert("Passport", f"{pd}d left", "ok")

    # Medical (also gated by the port minimum)
    md = _days_until(crew.get("medical_expiry"))
    if md is None:
        add_cert("Medical", "no date", "block"); findings.append("Medical certificate not provided.")
    elif md < 0:
        add_cert("Medical", "expired", "block"); findings.append("Medical certificate has expired.")
    elif md < min_medical_days:
        add_cert("Medical", f"{md}d < {min_medical_days}d", "block")
        findings.append(f"{port} requires medical validity of {min_medical_days}d; only {md}d remain.")
    elif md < 30:
        add_cert("Medical", f"{md}d left", "warn"); findings.append(f"Medical expires in {md} days.")
    else:
        add_cert("Medical", f"{md}d left", "ok")

    # Visa
    if visa_status == "Valid":
        add_cert("Visa", "valid", "ok")
    elif visa_status == "Expiring Soon":
        add_cert("Visa", "expiring", "warn"); findings.append("Visa expiring soon — renewal advised.")
    else:
        add_cert("Visa", str(visa_status).lower(), "block"); findings.append(f"Visa status: {visa_status}.")

    # STCW
    stcw = crew.get("stcw_status", "Unknown")
    if stcw == "Valid":
        add_cert("STCW", "valid", "ok")
    elif stcw == "Expiring Soon":
        add_cert("STCW", "expiring", "warn"); findings.append("One or more STCW certificates expiring.")
    else:
        add_cert("STCW", "invalid", "block"); findings.append("STCW certificates invalid or missing.")

    # Required certs for rank — show only the missing ones (a BLOCK), via REQUIRES.
    missing = [c for c in required_certs_for_rank(rank) if c not in held_certs]
    for c in missing:
        cid = f"cert:{c}"
        nodes.append({"id": cid, "type": "Certificate", "label": c, "sub": "missing", "status": "block", "x": cert_x, "y": 20 + row * 78})
        edges.append({"id": f"e_req_{c}", "source": port_id, "target": cid, "label": "REQUIRES", "status": "block"})
        row += 1
    if missing:
        findings.append(f"Missing required certifications for {rank or 'rank'}: {', '.join(missing)}.")

    verdict = _STATUS_TO_VERDICT[_worst([n["status"] for n in nodes] + [e["status"] for e in edges])]

    return {
        "nodes": nodes,
        "edges": edges,
        "findings": findings,
        "verdict": verdict,
        "backend": backend,
        "subject": {"crew_id": crew_id, "name": name, "rank": rank, "port": port},
    }


# ── AGE-backed retrieval (optional path) ───────────────────────────────────────


def _q_cypher(value: str) -> str:
    """Minimal escaping for string literals embedded inline in a Cypher query."""
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _unwrap_agtype(value: Any) -> Any:
    """run_cypher returns each column as a JSON-parsed agtype value. Strings come
    back JSON-quoted ("Filipino" → 'Filipino' after json.loads); numbers come back
    as int/float; vertices/edges come back as dicts. This collapses the strings
    to their bare value and leaves everything else alone."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "raw" in value:
        # fallback path in run_cypher when json.loads failed
        return str(value["raw"]).strip('"')
    return value


async def _age_port_restrictions(port: str) -> Optional[Dict[str, Any]]:
    """Fetch the port's restriction rules from the AGE maritime graph.

    Returns None when AGE is disabled, when the port isn't in the graph, or on any
    failure — caller falls back to the in-memory PORT_RESTRICTIONS dict. Importing
    lazily keeps compliance_graph importable when graph_db's SQLAlchemy engine
    hasn't been initialised (e.g. unit tests of the rules).
    """
    try:
        from L2Knowledge_graph.graph_db import age_enabled, run_cypher
    except Exception:
        return None
    if not age_enabled():
        return None
    try:
        port_q = _q_cypher(port)
        port_rows = await run_cypher(
            f"MATCH (p:Port {{name:'{port_q}'}}) RETURN p.min_medical_days"
        )
        if not port_rows:
            return None
        raw = port_rows[0]
        try:
            min_medical_days = int(raw) if isinstance(raw, (int, float)) else int(str(raw).strip('"'))
        except (TypeError, ValueError):
            min_medical_days = 30

        nat_rows = await run_cypher(
            f"MATCH (p:Port {{name:'{port_q}'}})-[:RESTRICTS]->(c:Country) RETURN c.name"
        )
        visa_required = [str(_unwrap_agtype(r)) for r in nat_rows if r is not None]
        return {"visa_required": visa_required, "min_medical_days": min_medical_days}
    except Exception as exc:
        log.warning("graph.age_port_restrictions_failed", error=str(exc), port=port)
        return None


async def get_compliance_subgraph(crew: Dict[str, Any], port: str) -> Dict[str, Any]:
    """Async dispatcher: prefer AGE-retrieved rules, fall back to the in-memory
    PORT_RESTRICTIONS dict on any failure. The returned shape is identical in
    both branches, only the `backend` field differs."""
    age_restrictions = await _age_port_restrictions(port)
    if age_restrictions is not None:
        return build_compliance_subgraph(
            crew, port, backend="age", port_restrictions=age_restrictions
        )
    return build_compliance_subgraph(crew, port, backend="fallback")
