"""
L2 Knowledge Graph — Dimension 2: OpsMap (process mining).

OpsMap is the *process* dimension of the L2 context graph. Where EntityMap answers
"what exists and how is it related", OpsMap answers "how does work ACTUALLY flow"
— discovered from the events the crew-change workflow emits at runtime, not from a
hand-drawn flowchart.

This mirrors the OpsMap dimension in the CognixOne reference architecture
(process mining via PM4Py → a directly-follows graph of activities, transition
frequencies/durations, process variants, bottlenecks and conformance), adapted to
this repo's stack: pure-Python mining over a captured event log, with the mined
process model optionally persisted into the SAME Apache AGE `maritime` graph as
process-step edges. No new datastore, no PM4Py dependency.

DESIGN NOTE — "no data duplication across dimensions" (see entity_map.py): OpsMap
does NOT re-create Crew/Vessel/Port nodes. It introduces its own (:Activity) nodes
(the steps of the crew-change process) and (:Activity)-[:NEXT]->(:Activity) edges,
and it annotates how often real cases moved a given crew through each step. The
canonical entities stay owned by EntityMap.

What it captures (the four process-mining concepts):
  * Process model  — the directly-follows graph (DFG): which activity follows which,
                     with frequency and average duration on each edge.
  * Variants       — the distinct end-to-end paths cases actually took (the "happy
                     path" vs the rejection path vs failures), with case counts.
  * Bottlenecks    — the handoffs where work waits longest (slowest DFG edges).
  * Conformance    — how many cases followed the intended crew-change path, and
                     where the deviations were.

Data source — "mine workflow events": every event WorkflowService relays through
its `_event_callback` (workflow_created, agent_completed, crew_updated,
auto_compliance, crew_signed_on, sign_on_rejected, workflow_failed, …) is recorded
here as one event-log entry keyed by workflow_id (the process-mining "case id").
build_process_graph() then orders each case by timestamp and derives the DFG.

Backend behaviour (same contract as compliance_graph / graph_db):
  * fallback (default): mining is pure Python over the in-memory event log — fully
    demoable with no AGE image. AGE persistence is skipped.
  * age: persist_process_model() additionally writes the mined DFG into the
    `maritime` graph so the process model is queryable in Cypher alongside EntityMap.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import structlog

from L2Knowledge_graph.graph_db import age_enabled, ensure_graph, run_cypher

log = structlog.get_logger()

# ── Process vocabulary ───────────────────────────────────────────────────────────
#
# The canonical activities of the crew-change process, in their intended order.
# Crew Matching / Travel / Notification run in PARALLEL during Phase 1, so they form
# an order-insensitive block (see _PARALLEL_BLOCK) for conformance purposes.
ACTIVITIES = [
    "Sign-Off Initiated",
    "Crew Matching",
    "Travel Arranged",
    "Crew Notified",
    "Sign-Off Confirmed",
    "Compliance Check",
    "Signed On",          # terminal — success
    "Sign-On Rejected",   # terminal — compliance failure
    "Workflow Failed",    # terminal — error
]

# The intended ("normative") path a clean sign-off → sign-on case should follow.
# Used for conformance scoring. The three parallel specialists are treated as a set.
HAPPY_PATH = [
    "Sign-Off Initiated",
    "Crew Matching",
    "Travel Arranged",
    "Crew Notified",
    "Sign-Off Confirmed",
    "Compliance Check",
    "Signed On",
]
_PARALLEL_BLOCK = {"Crew Matching", "Travel Arranged", "Crew Notified"}
_TERMINAL = {"Signed On", "Sign-On Rejected", "Workflow Failed"}

# WorkflowService-level event_type → activity. These events are case-scoped (carry a
# workflow_id) and have unambiguous business meaning, so they are the reliable spine
# of the mined process. Noisy stream events (agent_message/thinking, model_usage,
# agent_tool_use, master_routing, master_waiting) are intentionally NOT mapped.
EVENT_TO_ACTIVITY: Dict[str, str] = {
    "workflow_created": "Sign-Off Initiated",
    "sign_on_initiated": "Compliance Check",   # manual sign-on path enters at compliance
    "crew_updated": "Sign-Off Confirmed",
    "auto_compliance": "Compliance Check",
    "crew_signed_on": "Signed On",
    "sign_on_rejected": "Sign-On Rejected",
    "workflow_failed": "Workflow Failed",
}

# A completed specialist thread (event_type == "agent_completed") maps to an activity
# by which specialist finished. agent_name comes straight off the relayed event.
AGENT_COMPLETED_TO_ACTIVITY: Dict[str, str] = {
    "Crew Matching Agent": "Crew Matching",
    "Travel Agent": "Travel Arranged",
    "Notification Agent": "Crew Notified",
    "Compliance Agent": "Compliance Check",
}

# Who performs each activity — the actor shown on the reference (designed) process map,
# where there are no mined case counts to label a node with. Mirrors the actors the
# mined events carry (record_event stores the live actor per case).
ACTIVITY_ACTOR: Dict[str, str] = {
    "Sign-Off Initiated": "Master Agent",
    "Crew Matching": "Crew Matching Agent",
    "Travel Arranged": "Travel Agent",
    "Crew Notified": "Notification Agent",
    "Sign-Off Confirmed": "Master Agent",
    "Compliance Check": "Compliance Agent",
    "Signed On": "Compliance Agent",
    "Sign-On Rejected": "Compliance Agent",
    "Workflow Failed": "Master Agent",
}


# ── Event log (the "case" store) ──────────────────────────────────────────────────
#
# One append-only list of {case_id, activity, actor, ts_epoch, ts_iso}. In-memory by
# design: it is the process-mining working set, rebuilt as workflows run, and (when
# AGE is enabled) the DERIVED model — not the raw log — is what gets persisted.
_EVENT_LOG: List[Dict[str, Any]] = []
_LOG_LOCK = Lock()


def _parse_ts(value: Optional[str]) -> float:
    """ISO-8601 → epoch seconds. Falls back to now() so a missing/garbled timestamp
    never breaks ordering (it just sorts as 'now')."""
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return datetime.utcnow().timestamp()


def event_to_activity(event_type: str, agent_name: Optional[str]) -> Optional[str]:
    """Map a relayed workflow event to a canonical OpsMap activity, or None if the
    event is process-irrelevant noise. Pure function — also used by tests/docs."""
    if event_type in EVENT_TO_ACTIVITY:
        return EVENT_TO_ACTIVITY[event_type]
    if event_type == "agent_completed":
        return AGENT_COMPLETED_TO_ACTIVITY.get(agent_name or "")
    return None


# Record-specific fields worth keeping off a workflow event's data payload so the
# OpsMap detail views can answer "who signed off / on, whose case failed and why".
# Bulky/derived fields (notably the compliance subgraph) are intentionally dropped.
_DETAIL_KEYS = (
    "crew_name", "rank", "vessel", "crew_id",
    "candidate_name", "candidate_rank", "candidate_id", "crew_rank",
    "compliance_status", "compliance_score", "status", "pool",
    "error", "failures", "recommendation", "message",
)


def _curate(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep only the record-specific identity/outcome fields from an event payload."""
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in _DETAIL_KEYS:
        v = data.get(k)
        if v not in (None, "", [], {}):
            out[k] = v
    return out


def record_event(
    case_id: str,
    event_type: str,
    agent_name: Optional[str] = None,
    timestamp: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Record one workflow event into the OpsMap event log.

    Called from WorkflowService._event_callback for every emitted event. Returns
    True if the event mapped to an activity and was recorded, False if it was noise
    (so the caller can ignore the result). Consecutive duplicate activities within
    the same case are collapsed — a specialist thread can emit several
    agent_completed events and we only want the process step once. The curated
    `data` payload is stored on the event so per-case detail views can show the
    actual crew involved and the outcome reason.
    """
    if not case_id:
        return False
    activity = event_to_activity(event_type, agent_name)
    if not activity:
        return False
    ts_iso = timestamp or datetime.utcnow().isoformat()
    with _LOG_LOCK:
        # Collapse immediate repeats of the same activity for this case.
        for prior in reversed(_EVENT_LOG):
            if prior["case_id"] == case_id:
                if prior["activity"] == activity:
                    return False
                break
        _EVENT_LOG.append({
            "case_id": case_id,
            "activity": activity,
            "actor": agent_name or "Master Agent",
            "ts_epoch": _parse_ts(ts_iso),
            "ts_iso": ts_iso,
            "details": _curate(data),
        })
    return True


def record_trace(case_id: str, steps: List[Tuple[str, Optional[str], Optional[str]]]) -> int:
    """Record a whole ordered trace at once: list of (event_type, agent_name, ts_iso).
    Convenience for seeding captured sample traces and for tests. Returns the number
    of steps that mapped to activities."""
    return sum(record_event(case_id, et, an, ts) for (et, an, ts) in steps)


def reset_event_log() -> None:
    """Clear the event log (tests / re-seed)."""
    with _LOG_LOCK:
        _EVENT_LOG.clear()


def _cases() -> Dict[str, List[Dict[str, Any]]]:
    """Group the event log by case_id, each case sorted by timestamp."""
    by_case: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    with _LOG_LOCK:
        for e in _EVENT_LOG:
            by_case[e["case_id"]].append(e)
    for evs in by_case.values():
        evs.sort(key=lambda e: e["ts_epoch"])
    return by_case


# ── Mining ─────────────────────────────────────────────────────────────────────


def build_process_graph() -> Dict[str, Any]:
    """Mine the directly-follows graph (DFG) from the event log — the OpsMap process
    model. Nodes are activities (with the number of cases that hit them); edges are
    observed activity→activity transitions carrying frequency and average duration.

    Returns a React-Flow-ready payload (same envelope shape as EntityMap's
    search_subgraph) plus aggregate metrics, so the existing graph UI can render it.
    """
    by_case = _cases()
    node_cases: Dict[str, set] = defaultdict(set)            # activity -> {case_id}
    edge_count: Counter = Counter()                          # (a,b) -> n
    edge_durations: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    cycle_times: List[float] = []

    for case_id, evs in by_case.items():
        for e in evs:
            node_cases[e["activity"]].add(case_id)
        for a, b in zip(evs, evs[1:]):
            key = (a["activity"], b["activity"])
            edge_count[key] += 1
            edge_durations[key].append(max(0.0, b["ts_epoch"] - a["ts_epoch"]))
        if len(evs) >= 2:
            cycle_times.append(evs[-1]["ts_epoch"] - evs[0]["ts_epoch"])

    # Nodes — order by the canonical sequence so layout is stable; unknown activities
    # (shouldn't happen) fall to the end.
    def _rank(a: str) -> int:
        return ACTIVITIES.index(a) if a in ACTIVITIES else len(ACTIVITIES)

    nodes = [
        {
            "id": activity,
            "type": "Activity",
            "label": activity,
            "cases": len(cases),
            "terminal": activity in _TERMINAL,
        }
        for activity, cases in sorted(node_cases.items(), key=lambda kv: _rank(kv[0]))
    ]

    edges = []
    for (a, b), n in edge_count.items():
        durs = edge_durations[(a, b)]
        avg = sum(durs) / len(durs) if durs else 0.0
        edges.append({
            "id": f"{a}->{b}",
            "source": a,
            "target": b,
            "count": n,
            "avg_seconds": round(avg, 2),
            "label": f"{n}x · {_human_duration(avg)}",
        })
    edges.sort(key=lambda e: (-e["count"], e["source"]))

    return {
        "dimension": "OpsMap",
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "total_cases": len(by_case),
            "total_activities": len(nodes),
            "total_transitions": len(edges),
            "avg_cycle_time_seconds": round(sum(cycle_times) / len(cycle_times), 2) if cycle_times else 0.0,
            "avg_cycle_time_human": _human_duration(sum(cycle_times) / len(cycle_times)) if cycle_times else "—",
        },
    }


def reference_process_model() -> Dict[str, Any]:
    """The **reference (normative) process map** — the crew-change flow as DESIGNED,
    independent of any mined data. Where build_process_graph() discovers how work
    *actually* moved (and is empty until cases run), this returns the intended flow so
    a process map can always be shown: the happy path, the three specialists as a
    parallel block, and the compliance exception/error branches.

    Same `{dimension, nodes, edges, metrics}` envelope as build_process_graph() so the
    existing OpsMapGraph UI renders it with no new client contract. Edges additionally
    carry a `kind` (`happy` | `parallel` | `exception` | `error`) and nodes an `actor`,
    since there are no case counts to label the designed model with. Derived from
    HAPPY_PATH / _PARALLEL_BLOCK so the reference stays in step with the conformance
    definition; the terminal exception branches are defined explicitly.
    """
    spine = [a for a in HAPPY_PATH if a not in _PARALLEL_BLOCK]  # Initiated→Confirmed→Compliance→Signed On
    pre, post = "Sign-Off Initiated", "Sign-Off Confirmed"       # the parallel block sits between these
    block = [a for a in HAPPY_PATH if a in _PARALLEL_BLOCK]

    edges: List[Dict[str, Any]] = []

    def _add(a: str, b: str, kind: str, label: str = "") -> None:
        edges.append({
            "id": f"{a}->{b}", "source": a, "target": b,
            "count": 0, "avg_seconds": 0.0, "label": label, "kind": kind,
        })

    # Happy spine — but the direct pre→post hop is replaced by the parallel block.
    for a, b in zip(spine, spine[1:]):
        if (a, b) == (pre, post):
            continue
        _add(a, b, "happy", "pass" if (a, b) == ("Compliance Check", "Signed On") else "")
    # Parallel specialist block: fan out from `pre`, fan back in to `post`.
    for p in block:
        _add(pre, p, "parallel")
        _add(p, post, "parallel")
    # Exception / error terminals branch off the compliance decision point.
    _add("Compliance Check", "Sign-On Rejected", "exception", "fail")
    _add("Compliance Check", "Workflow Failed", "error", "error")

    def _rank(a: str) -> int:
        return ACTIVITIES.index(a) if a in ACTIVITIES else len(ACTIVITIES)

    activities = sorted(set(HAPPY_PATH) | {"Sign-On Rejected", "Workflow Failed"}, key=_rank)
    nodes = [
        {
            "id": a, "type": "Activity", "label": a,
            "cases": 0, "terminal": a in _TERMINAL,
            "actor": ACTIVITY_ACTOR.get(a, "Master Agent"),
        }
        for a in activities
    ]
    return {
        "dimension": "OpsMap",
        "model": "reference",
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "total_cases": 0,
            "total_activities": len(nodes),
            "total_transitions": len(edges),
            "avg_cycle_time_seconds": 0.0,
            "avg_cycle_time_human": "—",
        },
    }


def process_variants() -> Dict[str, Any]:
    """The distinct end-to-end paths cases actually took, ranked by frequency — the
    process-mining 'variants' view. Each variant carries case count, percentage and
    average cycle time. The 3 parallel specialists are interleaved in real order, so
    different interleavings legitimately show up as distinct variants (that IS the
    real process); use conformance() for the order-insensitive verdict."""
    by_case = _cases()
    total = len(by_case)
    buckets: Dict[Tuple[str, ...], List[float]] = defaultdict(list)
    for evs in by_case.values():
        path = tuple(e["activity"] for e in evs)
        cyc = (evs[-1]["ts_epoch"] - evs[0]["ts_epoch"]) if len(evs) >= 2 else 0.0
        buckets[path].append(cyc)

    variants = []
    for i, (path, cycs) in enumerate(sorted(buckets.items(), key=lambda kv: -len(kv[1]))):
        count = len(cycs)
        avg = sum(cycs) / count if count else 0.0
        terminal = path[-1] if path else None
        variants.append({
            "id": f"variant_{i+1}",
            "path": list(path),
            "case_count": count,
            "percentage": round(100.0 * count / total, 1) if total else 0.0,
            "avg_cycle_time_seconds": round(avg, 2),
            "avg_cycle_time_human": _human_duration(avg),
            "outcome": (
                "success" if terminal == "Signed On"
                else "rejected" if terminal == "Sign-On Rejected"
                else "failed" if terminal == "Workflow Failed"
                else "in_progress"
            ),
        })
    return {"total_cases": total, "variant_count": len(variants), "variants": variants}


def bottlenecks(limit: int = 5) -> Dict[str, Any]:
    """The slowest handoffs — DFG edges ranked by average duration. This is where
    work waits in the crew-change process (e.g. Sign-Off Confirmed → Compliance Check
    if documents take time to validate)."""
    graph = build_process_graph()
    ranked = sorted(graph["edges"], key=lambda e: -e["avg_seconds"])[:limit]
    return {
        "bottlenecks": [
            {
                "from": e["source"],
                "to": e["target"],
                "avg_seconds": e["avg_seconds"],
                "avg_human": _human_duration(e["avg_seconds"]),
                "occurrences": e["count"],
            }
            for e in ranked
        ]
    }


def conformance() -> Dict[str, Any]:
    """How closely the observed cases match the intended crew-change path
    (HAPPY_PATH), treating the 3 parallel specialists as an order-insensitive block.

    A case is conformant when it visits the required milestones in the required
    partial order and ends in 'Signed On'. Returns the conformance rate plus, for
    each non-conformant case, what deviated."""
    by_case = _cases()
    total = len(by_case)
    conformant = 0
    deviations: List[Dict[str, Any]] = []

    for case_id, evs in by_case.items():
        activities = [e["activity"] for e in evs]
        ok, reason = _is_conformant(activities)
        if ok:
            conformant += 1
        else:
            deviations.append({"case_id": case_id, "path": activities, "reason": reason})

    return {
        "total_cases": total,
        "conformant_cases": conformant,
        "conformance_rate": round(100.0 * conformant / total, 1) if total else 0.0,
        "happy_path": HAPPY_PATH,
        "deviations": deviations,
    }


def process_cases() -> Dict[str, Any]:
    """Per-case records — each mined crew-change case with the ACTUAL data behind it:
    who signed off, who was signed on (or rejected/failed and why), the compliance
    score, cycle time, and the ordered steps with their record-specific details.

    This is what the OpsMap detail views read to answer "whose case is this" — the
    aggregate views (process/variants/bottlenecks) intentionally stay anonymous."""
    by_case = _cases()
    cases: List[Dict[str, Any]] = []

    for case_id, evs in by_case.items():
        path = [e["activity"] for e in evs]
        terminal = path[-1] if path else None
        outcome = (
            "success" if terminal == "Signed On"
            else "rejected" if terminal == "Sign-On Rejected"
            else "failed" if terminal == "Workflow Failed"
            else "in_progress"
        )
        cyc = (evs[-1]["ts_epoch"] - evs[0]["ts_epoch"]) if len(evs) >= 2 else 0.0

        def _detail(activity: str, key: str) -> Any:
            for e in evs:
                if e["activity"] == activity:
                    return e.get("details", {}).get(key)
            return None

        # Rejection / failure reason for the human-readable summary.
        reason: Optional[str] = None
        if outcome == "rejected":
            failures = _detail("Sign-On Rejected", "failures")
            if isinstance(failures, list) and failures:
                reason = "; ".join(str(f) for f in failures)
            else:
                reason = _detail("Sign-On Rejected", "message")
        elif outcome == "failed":
            reason = _detail("Workflow Failed", "error")

        cases.append({
            "case_id": case_id,
            "sign_off_crew": _detail("Sign-Off Initiated", "crew_name"),
            "sign_off_rank": _detail("Sign-Off Initiated", "rank"),
            "sign_off_vessel": _detail("Sign-Off Initiated", "vessel"),
            "sign_on_crew": (
                _detail("Signed On", "crew_name")
                or _detail("Sign-On Rejected", "crew_name")
                or _detail("Compliance Check", "candidate_name")
            ),
            "outcome": outcome,
            "compliance_status": (
                _detail("Signed On", "compliance_status")
                or _detail("Sign-On Rejected", "compliance_status")
            ),
            "compliance_score": (
                _detail("Signed On", "compliance_score")
                if _detail("Signed On", "compliance_score") is not None
                else _detail("Sign-On Rejected", "compliance_score")
            ),
            "reason": reason,
            "cycle_time_seconds": round(cyc, 2),
            "cycle_time_human": _human_duration(cyc),
            "started_iso": evs[0]["ts_iso"] if evs else None,
            "ended_iso": evs[-1]["ts_iso"] if evs else None,
            "path": path,
            "steps": [
                {
                    "activity": e["activity"],
                    "actor": e["actor"],
                    "ts_iso": e["ts_iso"],
                    "details": e.get("details", {}),
                }
                for e in evs
            ],
        })

    cases.sort(key=lambda c: c.get("started_iso") or "", reverse=True)
    return {"total_cases": len(cases), "cases": cases}


def _is_conformant(activities: List[str]) -> Tuple[bool, Optional[str]]:
    """Partial-order conformance check against HAPPY_PATH. The parallel block may
    appear in any internal order but must sit between 'Sign-Off Initiated' and
    'Sign-Off Confirmed'. The case must terminate in 'Signed On'."""
    seq = [a for a in activities]
    if not seq:
        return False, "empty trace"
    if seq[0] != "Sign-Off Initiated":
        return False, f"started with '{seq[0]}', expected 'Sign-Off Initiated'"
    if seq[-1] != "Signed On":
        return False, f"ended in '{seq[-1]}', not 'Signed On'"
    present = set(seq)
    missing = [a for a in HAPPY_PATH if a not in present]
    if missing:
        return False, f"missing milestone(s): {', '.join(missing)}"

    # Milestone ordering (ignoring the internal order of the parallel block).
    def _first(a: str) -> int:
        return seq.index(a)

    if not (_first("Sign-Off Initiated")
            < min(_first(p) for p in _PARALLEL_BLOCK)
            and max(_first(p) for p in _PARALLEL_BLOCK)
            < _first("Sign-Off Confirmed")
            < _first("Compliance Check")
            < _first("Signed On")):
        return False, "milestones out of order"
    return True, None


def ops_map_summary() -> Dict[str, Any]:
    """Population summary — confirms OpsMap has mined data (mirrors EntityMap's
    summary). Cheap aggregate over the current event log."""
    graph = build_process_graph()
    variants = process_variants()
    conf = conformance()
    return {
        "dimension": "OpsMap",
        "activities": ACTIVITIES,
        "total_cases": graph["metrics"]["total_cases"],
        "total_activities": graph["metrics"]["total_activities"],
        "total_transitions": graph["metrics"]["total_transitions"],
        "variant_count": variants["variant_count"],
        "conformance_rate": conf["conformance_rate"],
        "avg_cycle_time_human": graph["metrics"]["avg_cycle_time_human"],
    }


def _human_duration(seconds: float) -> str:
    """Compact human duration for edge labels / metrics."""
    s = float(seconds or 0)
    if s < 1:
        return "<1s"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    if s < 86400:
        return f"{s/3600:.1f}h"
    return f"{s/86400:.1f}d"


# ── AGE persistence (overlay onto the maritime graph) ─────────────────────────────


async def persist_process_model() -> Dict[str, Any]:
    """Write the mined DFG into the AGE `maritime` graph as
    (:Activity {name})-[:NEXT {count, avg_seconds}]->(:Activity), overlaying it on
    the EntityMap nodes WITHOUT touching them. No-op (raises) under the fallback
    backend, matching build_entity_map()'s contract.

    Mirrors the CognixOne design: 'process model stored as graph edges in AGE
    (process step → next step with frequency and duration)'.
    """
    if not age_enabled():
        raise RuntimeError(
            "persist_process_model requires the AGE backend (GRAPH_BACKEND=age). "
            "Under fallback, OpsMap is still fully queryable via the API — it is "
            "mined in Python and does not need to be written to AGE."
        )
    await ensure_graph()
    graph = build_process_graph()

    def _q(v: Any) -> str:
        return str(v if v is not None else "").replace("\\", "\\\\").replace("'", "\\'")

    for n in graph["nodes"]:
        await run_cypher(
            f"MERGE (a:Activity {{name:'{_q(n['label'])}'}}) "
            f"SET a.cases={int(n['cases'])}, a.terminal={'true' if n['terminal'] else 'false'}"
        )
    for e in graph["edges"]:
        await run_cypher(
            f"MATCH (a:Activity {{name:'{_q(e['source'])}'}}), "
            f"(b:Activity {{name:'{_q(e['target'])}'}}) "
            f"MERGE (a)-[r:NEXT]->(b) "
            f"SET r.count={int(e['count'])}, r.avg_seconds={float(e['avg_seconds'])}"
        )
    log.info("ops_map.persisted", activities=len(graph["nodes"]), transitions=len(graph["edges"]))
    return {"persisted": True, **graph["metrics"]}
