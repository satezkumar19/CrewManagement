# L3 Fit Graph — Design Document

**Feature:** Derived L3 Intelligence Graph (the "fit graph") + live `intel_graph` stream
**Layer:** L3 Intelligence Graph
**Date:** 2026-06-05
**Status:** Implemented · backend logic verified · frontend type-checked
**Related:** [`L3_INTELLIGENCE_GRAPH.md`](./L3_INTELLIGENCE_GRAPH.md) (parent design), [`L3_TEST_PLAN.md`](./L3_TEST_PLAN.md)

---

## 1. Background & motivation

### 1.1 The problem

Before this change, "L3 Intelligence **Graph**" was a name, not a data structure. The
layer computed everything you would need for a graph — a vacancy, a candidate pool,
three scoring dimensions, scored/reasoned edges between them, and the L2 facts each
assessment consulted — but it **threw the structure away** into flat shapes:

- `IntelResult.candidates` — a sorted *list* of survivors.
- `IntelResult.reports` — a `crew_id → Assessment` *dictionary* per investigator.
- `ranking.fuse()` — a scalar weighted average (`0.50·crew + 0.30·vessel + 0.20·contract`)
  followed by `ranked.sort(...)`.

No nodes, no edges, no adjacency, no traversal. Three different things shared the word
"graph" and only one was real:

| "Graph" | What it actually was (before) |
| --- | --- |
| L3 "Intelligence Graph" | A *name* for the supervisor→3-investigators→fusion topology |
| Frontend `IntelligenceFlow.tsx` | A hand-drawn flow **diagram** (fixed JSX nodes/connectors) |
| L2 Knowledge/Compliance Graph (AGE) | The **only real** property graph (nodes/edges/Cypher) |

### 1.2 The decision

We considered two senses of "make L3 a graph":

- **Sense 1 — make L3's *output* a real graph.** Derive a node/edge graph from data the
  Supervisor already computes. No new infrastructure, no L2 dependency.
- **Sense 2 — persist L3's graph into AGE.** Write placements into the same Apache AGE
  store L2 uses, so they become queryable precedent history. Requires AGE enabled and a
  write path.

**We implemented Sense 1**, plus a tiny streamed `intel_graph` event so the graph
animates in live. Sense 1 is the highest value-for-effort: it makes the "Intelligence
Graph" name literal, produces exactly the structure Sense 2 and L4 would later persist,
and ships with zero new infra.

### 1.3 Goals / non-goals

**Goals**
- Turn each Supervisor run into a real, renderable node/edge graph.
- Stream it live so the UI "draws itself" as the run lands.
- Reuse the existing compliance-graph rendering vocabulary (status colors, x/y layout).
- Keep it a *pure transform* — no new dependencies, no L2 requirement, deterministic.

**Non-goals (this change)**
- Persisting the graph to AGE / making it queryable (that is Sense 2 / L4).
- Changing the Supervisor's orchestration, the investigators, or the fusion math.
- Building a generic graph engine — this is a purpose-built fit graph.

---

## 2. What the graph represents

One run = one graph. A four-layer, left-to-right layered structure:

```
 (Vacancy) ──assess──▶ (Candidate) ──score──▶ (Dimension) ──L2──▶ (L2 Fact)
                         │
                         └── disqualified candidates link to the BLOCKING dimension,
                             carrying the gate reason as the edge label
```

**Semantics**
- **Vacancy** — the sign-off context (vacated rank, port, vessel). One node.
- **Candidate** — each crew member assessed. Status encodes outcome:
  - `ok` (green) = shortlisted (ranked top-N)
  - `warn` (amber) = eligible but not in the top-N
  - `block` (red) = disqualified by a hard gate
- **Dimension** — the three investigator lenses (Crew Intel / Vessel Ops / Contract-Wage),
  each labeled with its fusion weight (50% / 30% / 20%).
- **L2 Fact** — the facts the investigators read from L2 (or its fallback rule data):
  the join port's nationality restrictions, and the rank's required safety certs. These
  tie the reasoning back to the L2 graph.

**Edges**
- `Vacancy → Candidate`: `shortlist #n` (ok) · `disqualified` (block) · `assessed` (warn)
- `Candidate → Dimension` (ranked only): the per-dimension score (e.g. `90`), colored
  `ok` if ≥ 0.5 else `warn` — the candidate's scoring breakdown.
- `Candidate → blocking Dimension` (disqualified only): the **gate reason** as the label
  (e.g. *"Missing vessel-mandated certs for Chief Officer: GMDSS"*), colored `block`.
- `Dimension → L2 Fact`: `L2 RESTRICTS` / `L2 REQUIRES`.

This is intentionally the same node/edge shape and `ok/warn/block` status vocabulary as
the existing **Compliance Context Graph**, so the proven React-Flow renderer style
applies directly.

---

## 3. Architecture & data flow

```
POST /api/v1/intelligence/match-context
        │
        ▼
IntelligenceSupervisor.find_replacements(context)
        │  load pool → run 3 investigators (parallel) → fuse → top-N
        ▼
build_fit_graph(context, candidates_by_id, reports, ranked, backend)   ◀── NEW
        │  pure transform → {nodes, edges, backend, node_count, edge_count}
        ├─▶ result.fit_graph = <graph>
        └─▶ emit "intel_graph" {nodes, edges, backend, counts}          ◀── NEW
        ▼
WebSocket /ws  ──broadcast──▶  frontend store (handleWSEvent)
        │                              │  intel.fitGraph = <graph>
        │                              ▼
        │                      IntelligenceGraph.tsx (React-Flow)        ◀── NEW
        │                         staggered node entrance animation
        ▼
HTTP response: IntelResult.to_dict() including fit_graph                 ◀── reconciled
```

Two delivery paths, both wired:
1. **Live** — the `intel_graph` WebSocket event arrives mid-run; the store sets
   `intel.fitGraph` and the component renders/animates immediately.
2. **Authoritative** — the HTTP response's `IntelResult.fit_graph` reconciles the final
   state (the store keeps the live graph if the payload omits it).

The graph is built and emitted in **both** outcomes — `matched` and `no_crew_found`
(where every candidate is a red `block` node) — so the dead-end is still explained
visually.

---

## 4. Backend implementation

### 4.1 `backend/agents/intelligence/fit_graph.py` (new)

The core builder. Key design points:

- **Pure & deterministic.** Depends only on `ranking._key_for`, `schemas`, and the data
  passed in. No I/O, no clock, no randomness → stable output for the same run (matches
  the rest of L3's deterministic stance, so tests have stable expectations).
- **Layered layout.** Four fixed columns (`x = 0 / 240 / 520 / 780`), `GAP = 90` between
  rows. `_column_ys(count, max_rows)` vertically centers each column independently so the
  graph looks balanced; React-Flow `fitView` handles final scaling. Nodes are `draggable`
  so users can rearrange.
- **Bounded.** Candidates capped at `_MAX_CANDIDATES = 12` (ranked first, then
  disqualified, then eligible-not-ranked); any overflow is surfaced as `+N more` on the
  vacancy node rather than silently dropped.
- **Classification.** `_disqualifying(crew_id)` scans reports in `crew → vessel →
  contract` order and returns the first hard gate `(dimension_key, reason)`; this both
  flags the candidate red and picks the dimension its block edge points at.
- **L2 fact extraction.**
  - Port rules: read from the Vessel Ops report's `applied`
    (`l2_port_restricted_nationalities`, `join_port`, `l2_backend`).
  - Safety certs: pulled off the first Crew Intel assessment carrying
    `signals.l2_required_safety_certs` (these come from L2's `Rank-[:REQUIRES]->Certificate`
    edges).
  - Backend label resolved from what an investigator actually recorded, falling back to
    the passed `backend`.

**Return shape**
```python
{
  "nodes": [{ "id", "type", "label", "sub", "status", "x", "y" }, ...],
  "edges": [{ "id", "source", "target", "label", "status" }, ...],
  "backend": "age" | "fallback",
  "node_count": int,
  "edge_count": int,
}
```

### 4.2 `schemas.py`

`IntelResult` gained `fit_graph: Optional[Dict[str, Any]] = None`, included in
`to_dict()` so the API returns it.

### 4.3 `supervisor.py`

After `fuse()` and the `intel_ranking` / `intel_no_crew` emit, and **before**
notifications:

```python
result.fit_graph = build_fit_graph(
    context, candidates_by_id, list(reports), ranked, backend=l2_backend()
)
await emit("intel_graph", {
    "workflow_id": context.workflow_id,
    "nodes": result.fit_graph["nodes"],
    "edges": result.fit_graph["edges"],
    "backend": result.fit_graph["backend"],
    "node_count": result.fit_graph["node_count"],
    "edge_count": result.fit_graph["edge_count"],
})
```

`l2_backend` is imported from `graph_gateway.backend` — the same seam the investigators
use, so the reported backend is consistent with the actual L2 reads.

### 4.4 New event: `intel_graph`

Slots into the existing streamed sequence (Section 6 of the parent doc):

```
intel_supervisor_started
  intel_investigator_started   × 3
  intel_investigator_completed × 3
intel_ranking | intel_no_crew
intel_graph                      ◀── NEW (node/edge fit graph)
intel_notification_sent  × N
intel_supervisor_completed
```

It uses the same `event_callback → WebSocket` vocabulary as every other `intel_*` event,
so no streaming-layer changes were needed.

---

## 5. Frontend implementation

### 5.1 `frontend/src/components/intelligence/IntelligenceGraph.tsx` (new)

React-Flow 11 view (already a project dependency, used by `ComplianceGraph.tsx`):

- Reads `intel.fitGraph` from the store. Renders nothing until a graph exists.
- **Custom `IntelNode`** mirrors the compliance node: status-colored ring + type-colored
  left accent (`Vacancy` cyan / `Candidate` sky / `Dimension` violet / `L2Fact` slate).
- **Live entrance animation.** Each node is a `motion.div` with
  `initial={{opacity:0, scale:0.82}} → animate` and a **staggered** `delay = index*0.06s`
  spring. A per-run generation stamp (`intel.startedAt`) is passed as `data.gen` and used
  as the motion `key`, so nodes **remount and re-animate on every run** — the graph
  "draws itself" each time.
- Status-colored edges; `block` edges are `animated` (flowing dashes) to draw the eye to
  disqualifications. Arrow markers, label backgrounds — same styling as the compliance
  graph.
- Footer legend (Shortlisted / Assessed / Disqualified) + live `L2 backend: …` label.

### 5.2 Store — `frontend/src/store/workflowStore.ts`

- `IntelRunState` gained `fitGraph: IntelFitGraph | null`; initialized null, reset to null
  on `startIntelRun`.
- `handleWSEvent` handles the `intel_graph` case → builds `fitGraph` from the payload.
- `setIntelResult` reconciles: `fitGraph: result.fit_graph ?? s.intel.fitGraph` (final
  result is authoritative but won't wipe a live graph if it omits one).
- `intelLabel` gained a human-readable line: *"Fit graph built — N nodes, M edges"*.

### 5.3 Types — `frontend/src/types/index.ts`

Added `IntelGraphNode`, `IntelGraphEdge`, `IntelFitGraph` (reusing the existing
`GraphStatus` union), plus `IntelResult.fit_graph?` and `IntelRunState.fitGraph`.

### 5.4 Panel — `frontend/src/components/intelligence/IntelligencePanel.tsx`

Renders `<IntelligenceGraph />` directly under the workflow pipeline diagram, so a run
shows: pipeline flow → derived fit graph → ranked results.

---

## 6. Files changed

| File | Change |
| --- | --- |
| `backend/agents/intelligence/fit_graph.py` | **new** — `build_fit_graph()` |
| `backend/agents/intelligence/supervisor.py` | build graph, set `fit_graph`, emit `intel_graph` |
| `backend/agents/intelligence/schemas.py` | `IntelResult.fit_graph` + serialization |
| `frontend/src/components/intelligence/IntelligenceGraph.tsx` | **new** — React-Flow view + animation |
| `frontend/src/components/intelligence/IntelligencePanel.tsx` | mount the graph component |
| `frontend/src/store/workflowStore.ts` | handle `intel_graph`, store/reset/reconcile `fitGraph`, label |
| `frontend/src/types/index.ts` | fit-graph types + fields |
| `docs/L3_INTELLIGENCE_GRAPH.md` | document new file, event, and concept |
| `docs/L3_FIT_GRAPH_DESIGN.md` | **new** — this document |

---

## 7. Design decisions & rationale

- **Derive, don't re-engineer.** The graph is a pure projection of existing run data, so
  the Supervisor/investigators/fusion are untouched and the change is low-risk and
  testable in isolation.
- **Reuse the compliance graph contract.** Same `{id,type,label,sub,status,x,y}` nodes,
  same `ok/warn/block` vocabulary, same React-Flow patterns → minimal new surface area and
  visual consistency.
- **Build it in the Supervisor, not the API.** Only the Supervisor has the full
  `candidates_by_id` (needed for names/ranks of *disqualified* candidates, which the flat
  `reports` don't carry).
- **Stream + reconcile.** Live event for responsiveness; HTTP `fit_graph` for the
  authoritative final state — the same dual-path pattern the rest of the L3 run already
  uses.
- **Bounded with a visible note.** Capping at 12 candidates keeps the graph readable;
  surfacing `+N more` avoids the "silent truncation reads as complete coverage" trap.
- **Backend label honesty.** The graph reports `age` vs `fallback` from the same gateway
  the investigators used, so the UI never implies a live graph query that didn't happen.

---

## 8. Verification

- **Backend logic** — `build_fit_graph` validated in isolation (bypassing the heavy
  `agents` package init) against a synthetic *Chief Officer @ Singapore* run exercising
  all four candidate states (ranked #1/#2, cert-disqualified, eligible-not-ranked) and an
  L2 port restriction. 8 assertions pass: vacancy/dimension/candidate/L2-fact nodes
  present, disqualified candidate links to `dim_vessel` with the gate reason, ranked
  candidate has 3 scored edges, `fact_port` shows the restriction (warn), `fact_certs`
  carries the L2 required certs, and all edge ids are unique.
- **Frontend** — `tsc --noEmit` → exit 0.

**Not run here:** the full FastAPI server (this session's Python was 3.14 with project
deps absent; the dev env is 3.12). End-to-end verification = run the backend + frontend
dev servers and hit **Run Match**; the `intel_graph` event streams over `/ws` and the
graph animates in.

---

## 9. Future work

- **Sense 2 — persist to AGE.** Write the fit graph into the AGE store as
  `(:Candidate)-[:ASSESSED_BY {score}]->(:Vacancy)` etc., enabling precedent queries
  (e.g. *"CO placements at Singapore scoring > 80"*). Requires `age_enabled()` and a write
  path.
- **L4 feedback.** `IntelResult.fit_graph` is exactly the trace L4's Decision Graph can
  record; L4's Precedent Index could later feed back as an additional fusion signal.
- **Richer L2 facts.** Expand fact nodes (medical-validity windows, port departure
  schedule, contract envelope) as more L2 reads are surfaced as signals.
- **Interaction.** Click a candidate to expand full per-dimension rationale; highlight the
  path from a fact to the candidates it gated.
```
