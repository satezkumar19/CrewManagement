# L2 Knowledge Graph — OpsMap Dimension Design

**Layer:** L2 — Knowledge Graph · **Dimension 2 of 3:** OpsMap (process mining)
**Stack:** pure-Python mining over a captured event log + optional Apache AGE persistence
**Status:** Implemented (`backend/L2Knowledge_graph/ops_map.py`)
**Reference:** CognixOne OpsMap (process mining → directly-follows graph, variants, bottlenecks, conformance)

---

## 1. What OpsMap is

Where **EntityMap** answers *"what exists and how is it related"*, **OpsMap** answers
*"how does work actually flow"*. It is the process dimension of L2: a graph of the
**activities** the crew-change process moves through and the **transitions** between
them, **discovered from the events the system emits at runtime** — not drawn by hand.

This mirrors the OpsMap dimension in the CognixOne reference architecture, where
process mining (PM4Py) turns an event log into a directly-follows graph with
frequency/duration on each edge, plus variant, bottleneck and conformance views. We
implement the same four concepts, adapted to this repo:

| Concept | In CognixOne | In this repo |
|---|---|---|
| Process model | PM4Py DFG, stored as AGE edges | `build_process_graph()` — DFG mined in Python; optionally persisted as `(:Activity)-[:NEXT]->(:Activity)` in AGE |
| Variants | Distinct paths through the process | `process_variants()` |
| Bottlenecks | Slowest handoffs | `bottlenecks()` |
| Conformance | % matching the intended path | `conformance()` |

**Key difference from EntityMap:** EntityMap *requires* AGE (the canonical graph lives
there). OpsMap is mined in Python from an in-memory event log, so it works under the
**fallback** backend too — exactly like `compliance_graph.py`. AGE is only needed to
*persist* the mined model alongside EntityMap.

---

## 2. "No data duplication across dimensions"

Per the core L2 constraint (see `entity_map.py`), OpsMap does **not** re-create
Crew/Vessel/Port nodes. It introduces its own node label, `(:Activity)`, for the steps
of the crew-change process, and `(:Activity)-[:NEXT]->(:Activity)` edges annotated with
how often and how fast real cases moved through each step. The canonical entities stay
owned by EntityMap; OpsMap is an **overlay**.

---

## 3. Data source — mining workflow events

The process is mined from the events `WorkflowService` already relays through its
`_event_callback`. A one-line hook records each event into the OpsMap event log, keyed
by `workflow_id` (the process-mining **case id**):

```
WorkflowService._event_callback(event_type, agent_name, data)
   └─> ops_map.record_event(data["workflow_id"], event_type, agent_name, ts)
```

Capture is wrapped in `try/except` so it can never break the live workflow or the
WebSocket stream. Only meaningful, case-scoped events become activities; stream noise
(`agent_message`, `agent_thinking`, `model_usage`, `agent_tool_use`, `master_routing`,
`master_waiting`) is intentionally ignored.

### Activity vocabulary

| Activity | Mined from event | Actor |
|---|---|---|
| Sign-Off Initiated | `workflow_created` | Master Agent |
| Crew Matching | `agent_completed` (Crew Matching Agent) | specialist |
| Travel Arranged | `agent_completed` (Travel Agent) | specialist |
| Crew Notified | `agent_completed` (Notification Agent) | specialist |
| Sign-Off Confirmed | `crew_updated` | Master Agent |
| Compliance Check | `auto_compliance` / `sign_on_initiated` / `agent_completed` (Compliance Agent) | Master / Compliance |
| Signed On *(terminal)* | `crew_signed_on` | Compliance Agent |
| Sign-On Rejected *(terminal)* | `sign_on_rejected` | Compliance Agent |
| Workflow Failed *(terminal)* | `workflow_failed` | Master Agent |

Crew Matching / Travel / Notification run **in parallel** in Phase 1, so their order
varies between cases — that is real process behaviour and shows up honestly in the
variants. Conformance treats them as an **order-insensitive block**.

---

## 4. Mining algorithm

`build_process_graph()` groups the event log by `case_id`, sorts each case by
timestamp, and derives a **directly-follows graph (DFG)**:

- **Nodes** = activities, each carrying the number of distinct cases that hit it.
- **Edges** = observed `a → b` transitions, carrying `count` (frequency) and
  `avg_seconds` (mean wait between the two events across all cases).
- **Cycle time** = last-event minus first-event timestamp, averaged across cases.

`process_variants()` buckets cases by their exact ordered activity sequence and ranks
the buckets by frequency, tagging each with an outcome (`success` / `rejected` /
`failed` / `in_progress`).

`bottlenecks()` ranks DFG edges by `avg_seconds` — the handoffs where work waits
longest (e.g. *Sign-Off Confirmed → Compliance Check* when documents take time).

`conformance()` checks each case against the normative `HAPPY_PATH` using a
**partial-order** rule: it must start at *Sign-Off Initiated*, contain every milestone,
keep the milestone ordering (the 3 specialists may interleave freely between
*Initiated* and *Confirmed*), and end in *Signed On*. Non-conformant cases are returned
with the reason they deviated.

The output envelope of `build_process_graph()` matches EntityMap's
`search_subgraph()` (`{nodes, edges}` with ids/labels), so the existing React-Flow
graph UI can render the process map with no new client contract.

---

## 4a. Reference (designed) process map — `reference_process_model()`

The mined DFG (§4) is **discovered** — it is empty until workflows run and only ever
shows transitions that actually occurred. Alongside it, OpsMap exposes a **reference
(normative) process map**: the crew-change flow *as designed*, built from the activity
vocabulary rather than from data, so a process map is **always** available.

- **Same envelope** as `build_process_graph()` (`{dimension, nodes, edges, metrics}`),
  so the existing `OpsMapGraph` UI renders it unchanged. `model: "reference"` flags it.
- **Derived, not hand-listed:** the happy spine and the parallel specialist block come
  from `HAPPY_PATH` / `_PARALLEL_BLOCK`, so the reference cannot drift from the
  conformance definition. The two terminal exception branches
  (`Compliance Check → Sign-On Rejected`, `Compliance Check → Workflow Failed`) are
  defined explicitly.
- **Extra fields for the designed view:** each node carries an `actor` (there are no
  case counts to show), and each edge a `kind` ∈ {`happy`, `parallel`, `exception`,
  `error`} so the UI can colour the spine, dash the concurrent block, and flag the
  exception/error branches.

In the UI this is a **Discovered ⇄ Reference toggle** on the OpsMap view; the reference
map also doubles as the empty-state visual (the page opens on it when no cases have been
mined yet). Discovered answers *"how did work actually flow"*; Reference answers *"how is
it supposed to flow"* — and the gap between them is exactly what `conformance()` scores.

## 5. API (`/api/v1/graph/opsmap/...`)

All read endpoints work under **both** backends (they return empty structures, not
503, before any workflow has run):

| Endpoint | Returns |
|---|---|
| `GET /opsmap/summary` | cases mined, activities, transitions, variant count, conformance rate, avg cycle time |
| `GET /opsmap/process` | the React-Flow-ready **discovered** DFG (nodes + edges with frequency/duration) |
| `GET /opsmap/reference` | the **reference (designed)** process map — see §4a; always populated, even with 0 cases |
| `GET /opsmap/variants` | distinct paths ranked by frequency, with outcome + cycle time |
| `GET /opsmap/bottlenecks?limit=` | slowest handoffs |
| `GET /opsmap/conformance` | happy-path conformance rate + per-case deviations |
| `POST /opsmap/persist` | write the mined model into AGE *(requires `GRAPH_BACKEND=age`)* |

---

## 6. Running it

OpsMap self-populates as workflows run (events are captured live). To see a populated
process graph **before** any live runs, replay the captured sample traces:

```bash
cd backend
# print the mined model from 4 captured sample crew-change traces
python -m L2Knowledge_graph.scripts.seed_ops_map --demo

# also persist the mined DFG into the AGE maritime graph (needs GRAPH_BACKEND=age)
GRAPH_BACKEND=age python -m L2Knowledge_graph.scripts.seed_ops_map --demo --persist
```

> The event log is in-memory **per process**. The seed script populates the log in its
> own process (for offline inspection / AGE persistence). Inside the live API process
> the log fills from real workflow events; the OpsMap endpoints read that live log.

### Verified behaviour (4 sample traces: 2 happy, 1 rejection, 1 failure)

- `total_cases = 4`, `total_activities = 9`, `total_transitions = 11`
- `conformance_rate = 50%` (the 2 happy-path cases conform regardless of specialist interleaving; rejection + failure do not)
- top bottleneck correctly identified as *Sign-Off Confirmed → Compliance Check* (the deliberately slow handoff in the rejection case)
- 4 variants surfaced with outcomes `success`, `success`, `rejected`, `failed`

---

## 7. Once AGE is enabled — querying the model in Cypher

After `POST /opsmap/persist` (or `--persist`), the process model is queryable next to
EntityMap in the same `maritime` graph:

```cypher
-- the discovered crew-change flow with frequencies
MATCH (a:Activity)-[r:NEXT]->(b:Activity)
RETURN a.name, b.name, r.count, r.avg_seconds
ORDER BY r.count DESC;

-- the bottleneck handoff
MATCH (a:Activity)-[r:NEXT]->(b:Activity)
RETURN a.name, b.name, r.avg_seconds ORDER BY r.avg_seconds DESC LIMIT 1;
```

---

## 8. Next: OrgMap

OrgMap (dimension 3) follows the same overlay pattern: add `(:Company)` / `(:Fleet)`
nodes and `OPERATES` / `BELONGS_TO` edges pointing at the existing EntityMap `Vessel`
nodes, plus communication-pattern edges (handoff frequency between actors) derived from
the same captured event-log actors OpsMap already records.
