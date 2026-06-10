# L2 Knowledge Graph — Baseline Design & Test Doc

**Layer:** L2 — Knowledge Graph
**Stack:** PostgreSQL 16 + Apache AGE 1.6.0 (openCypher)
**Prototype:** Jun 10 · **Prod:** Jun 15 · **Doc due:** Jun 12 (async review)
**Status of this baseline:** EntityMap **implemented & verified**; OpsMap **implemented**
(process-mining DFG — see [`OPSMAP_DESIGN.md`](OPSMAP_DESIGN.md)); OrgMap **implemented & verified**
(org hierarchy + manning gaps — see §5.2). **All three dimensions are now built and seeded
into the live AGE `maritime` graph.**

---

## 1. Purpose & scope

L2 turns the raw, streaming records that L1 (SignalFabric) lands into a **connected
knowledge graph** that the L3 Intelligence layer can traverse instead of joining flat
tables. The graph is built with **Apache AGE inside the same PostgreSQL instance** that
holds the relational `crew` table — no new datastore is introduced, which is the core
architectural constraint (see `database/graph_db.py`).

The plan defines **three graph dimensions** over one shared set of nodes:

| Dimension | Question it answers | Status |
|-----------|--------------------|--------|
| **EntityMap** | *What exists and how is it factually related?* (Crew, Vessel, Port, Contract, Certification) | **Built** |
| **OpsMap** | *What is the operational state?* (sign-off → search → match → onboard) | **Built** (process mining) |
| **OrgMap** | *How is the org structured?* (company → fleet → vessel → rank) | **Built** (hierarchy + manning gaps) |

**Exit criteria (from the plan) and where each is met:**

| Exit criterion | Met by |
|----------------|--------|
| All 3 dimensions populated | EntityMap + OpsMap + OrgMap all populated in the live graph (§3.4, §5) |
| Crew search by rank + cert + port returns correctly | `GET /api/v1/graph/crew/search` (§6), verified §7 |
| Full relationship traversal works | `GET /api/v1/graph/crew/{id}/traverse` (§6), verified §7 |
| Test UI query < 3 s | Measured 5–37 ms (§7) |
| No data duplication across dimensions | Single-node MERGE on business keys (§4) |

---

## 2. Why Postgres + AGE (not a separate graph DB)

- The relational `crew` table and the `maritime` graph live in **one Postgres**, so a
  decision can join graph context to relational rows in a single connection.
- AGE speaks **openCypher**, so L3 reads idiomatic graph queries.
- Backend is switchable via `GRAPH_BACKEND` (`fallback` | `age`). Under `fallback` the
  app builds an equivalent compliance subgraph in pure Python (demoable with no AGE
  image); under `age` it runs Cypher against the seeded graph. **The shape returned to
  callers is identical**, so L3 / the API / the frontend never learn which backend ran.

The only module that speaks Cypher is `database/graph_db.py` (`run_cypher`,
`ensure_graph`). Everything else calls typed Python functions.

---

## 3. Graph schema — EntityMap (implemented)

### 3.1 Node labels (5 entity types)

| Label | Business key | Properties |
|-------|-------------|-----------|
| `Crew` | `crew_id` | `name, rank, grade, nationality, port, vessel, status, pool, experience_years` |
| `Vessel` | `name` | — (identity node; org/ops edges attach here) |
| `Port` | `name` | — |
| `Certificate` | `type` | — |
| `Contract` | `contract_id` | `rank, vessel, port, start_date, status` |

### 3.2 Edge types (EntityMap relationships)

```
(:Crew)-[:HOLDS]->(:Certificate)        crew qualifications
(:Crew)-[:ASSIGNED_TO]->(:Vessel)       current ship (onboard crew)
(:Crew)-[:CURRENTLY_AT]->(:Port)        where the crew physically is
(:Vessel)-[:CALLS_AT]->(:Port)          ship's port of call
(:Crew)-[:SIGNED]->(:Contract)          the engagement signed
(:Contract)-[:FOR_VESSEL]->(:Vessel)    contract's ship
(:Contract)-[:AT_PORT]->(:Port)         contract's joining port
```

### 3.3 ASCII overview

```
                 HOLDS
   ┌────────────────────────────► (:Certificate)
   │
(:Crew) ──CURRENTLY_AT──► (:Port) ◄──CALLS_AT── (:Vessel)
   │  │                              ▲              ▲
   │  └────ASSIGNED_TO───────────────┘              │
   │                                                │
   └──SIGNED──► (:Contract) ──FOR_VESSEL────────────┘
                     │
                     └──AT_PORT──► (:Port)
```

### 3.4 Populated counts (current seed: 40 crew)

**EntityMap (dimension 1):**
```
Nodes: Crew=40  Vessel=5  Port=9  Certificate=16  Contract=20   (90 total)
Edges: HOLDS=56  ASSIGNED_TO=20  CURRENTLY_AT=40  CALLS_AT=13
       SIGNED=20  FOR_VESSEL=20  AT_PORT=20                    (189 total)
```

**OrgMap overlay (dimension 3 — adds nodes/edges, reuses the Vessel/Crew above):**
```
New nodes: Company=2  Fleet=4  Rank=14                          (20 added)
New edges: OWNS=4  OPERATES=5  REQUIRES_RANK=55  HAS_RANK=40    (104 added)
```

**Whole live graph (all dimensions, OpsMap persisted on demand):** 110 nodes, 293 edges.
Verified against the running `crew-postgres` AGE graph on `2026-06-09`. The OrgMap
numbers prove the no-duplication rule (§4): still exactly **5** Vessel nodes after the
overlay attached 5 `OPERATES` + 55 `REQUIRES_RANK` edges to them.

---

## 4. No data duplication across dimensions

Every node is **MERGEd on its business key**, never CREATEd blindly. Consequences:

- A vessel named `MV Pacific Star` is **one** node, even though 4 crew are assigned to it
  and the OrgMap overlay also points a `(:Fleet)-[:OPERATES]->` edge at it (verified: 5
  Vessel nodes before and after the OrgMap seed — §3.4).
- OpsMap and OrgMap add **edges and at most new node types** (`Company`, `Fleet`,
  state markers) — they never re-create `Crew`/`Vessel`/`Port`/`Certificate`.
- Proof from the seed: 5 vessels and 9 ports as **distinct** nodes despite 40 crew rows.

This is what makes "three dimensions" a single coherent graph rather than three copies.

---

## 5. OpsMap (built) & OrgMap (built)

> OpsMap **and** OrgMap are now **implemented**. Both reuse EntityMap nodes (§4) and add only
> the edges/nodes named, per the shared-node contract (§5.3). OrgMap shipped in commits
> `e71e5ce` (dimension) and `d2494cd` (filtered views + vessel hierarchy refinements).

### 5.1 OpsMap — operational-state overlay *(implemented as process mining)*

> **Implemented** in [`L2Knowledge_graph/ops_map.py`](../ops_map.py); full design in
> [`OPSMAP_DESIGN.md`](OPSMAP_DESIGN.md). The shipped implementation **supersedes the
> original `Stage`-node sketch** below: rather than hand-drawn pipeline stages, OpsMap
> **mines a directly-follows graph (DFG)** from the workflow event log at runtime
> (mirroring the CognixOne / PM4Py process-mining model), yielding frequency/duration on
> each transition plus variant, bottleneck and conformance views.

As built, OpsMap answers *"how does work actually flow"* over existing `Crew` nodes:

- **New node type:** `Activity {name, cases}` — a discovered step of the crew-change
  process (`Sign-Off Initiated → Crew Matching → … → Signed On`), **not** a fixed
  hand-drawn stage list.
- **New edge:** `(:Activity)-[:NEXT {count, avg_seconds}]->(:Activity)` — an observed
  transition, annotated with how often and how fast real cases moved through it.
- **Build source:** `WorkflowService._event_callback` hooks each runtime event into the
  OpsMap event log (keyed by `workflow_id` = case id); `build_process_graph()` mines the
  DFG. Works under **both** backends — mined in Python, optionally persisted to AGE.
- **Endpoints:** `GET /api/v1/graph/opsmap/{summary,process,variants,bottlenecks,conformance}`,
  `POST /opsmap/persist`.

<details><summary>Original §5.1 sketch (superseded — kept for review history)</summary>

Models the sign-off → onboarding lifecycle as graph state over existing `Crew` nodes.

- **New node type:** `Stage` `{name}` with the fixed pipeline
  `SignOff → Search → Match → Onboard`.
- **New edges:**
  - `(:Crew)-[:AT_STAGE {since}]->(:Stage)` — a crew's current pipeline position.
  - `(:Crew)-[:MATCHED_TO {score, reason}]->(:Vessel)` — the Crew Matching Agent's
    output written back as a graph edge (replaces the relational `match_score` column
    as the graph-native source of truth).
  - `(:Crew)-[:BACKFILLS]->(:Crew)` — sign-on candidate proposed to replace a
    signing-off crew, the headline OpsMap multi-hop:
    `signing-off Crew → Vessel → required rank → candidate Crew`.
- **Build source:** `services/workflow_service.py` emits stage transitions; an
  `ops_map.py` builder (mirrors `entity_map.py`) MERGEs the edges per workflow event.
- **Key query:** *"for vessel V losing crew C, who can backfill?"* →
  `(c:Crew)-[:ASSIGNED_TO]->(:Vessel)<-[:MATCHED_TO]-(cand:Crew)-[:AT_STAGE]->(:Stage {name:'Search'})`.

</details>

### 5.2 OrgMap — organizational hierarchy overlay *(implemented)*

> **Implemented** in [`L2Knowledge_graph/org_map.py`](../org_map.py) with source data in
> [`org_data.py`](../org_data.py); seeded by
> [`scripts/seed_org_map.py`](../scripts/seed_org_map.py). Models ownership/manning
> structure **above** the vessel and overlays it on the existing EntityMap `Vessel`/`Crew`
> nodes (MATCH + MERGE only, §5.3).

- **New node types (3):** `Company {name}`, `Fleet {name}`, `Rank {name}`.
- **New edge types (4):**
  - `(:Company)-[:OWNS]->(:Fleet)-[:OPERATES]->(:Vessel)` (Vessel reused from EntityMap).
  - `(:Vessel)-[:REQUIRES_RANK {required}]->(:Rank)` — manning requirement per ship.
  - `(:Crew)-[:HAS_RANK]->(:Rank)` — promotes the `Crew.rank` property to a shared node
    so org-level queries ("all Masters in Fleet X") traverse instead of scanning.
- **Source data (`org_data.py`):** `ORG_TREE` distributes the 5 EntityMap vessels across
  **2 companies / 4 fleets** (Oceanic Shipping Lines → Pacific/Atlantic Fleet, Meridian
  Maritime → Tanker/Mediterranean); `MANNING` is a standard per-vessel rank template
  (12 berths/ship). Ranks derive from the `RANKS` vocabulary in `mock_data/crew_data.py`.
- **Builder (`build_org_map()`):** idempotent (all `MERGE`); requires EntityMap seeded
  first because it `MATCH`es the Vessel/Crew nodes. Produced **20 new nodes** (Company 2,
  Fleet 4, Rank 14) and **104 new edges** (OWNS 4, OPERATES 5, REQUIRES_RANK 55,
  HAS_RANK 40) in the live seed (§3.4).
- **Headline query — manning gap (`manning_gap()`):** *"how many of each rank does a scope
  need vs. have?"* `required` = Σ `REQUIRES_RANK.required` over scope vessels; `have` =
  `(:Crew)-[:ASSIGNED_TO]->(:Vessel)` counted by rank; `gap = required − have` (positive =
  short-staffed, rows sorted most-short-staffed first). **Scope is selectable**: a single
  vessel, a fleet, a company's whole fleet, or the entire org.
  *Live example* — `?company=Oceanic Shipping Lines` (3 vessels) returns AB Seaman
  required 6 / have 1 / gap **5**, Master 3 / 0 / gap **3**, Bosun 3 / 0 / gap **3**, …
- **Structure view (`org_structure()`):** returns the Company → Fleet → Vessel hierarchy
  in the same React-Flow `{nodes, edges}` envelope as EntityMap's subgraph, so the Graph
  page renders it directly.

**AGE implementation gotchas captured during the OrgMap build:**
1. The manning edge property is named **`required`, not `count`** — `count` collides with
   the Cypher `COUNT()` function in this AGE build and fails to parse.
2. `org_structure()` queries the two hops **separately** (`Company-[:OWNS]->Fleet` and
   `Fleet-[:OPERATES]->Vessel`) rather than as one two-edge path — a single
   `(Company)-[:OWNS]->(Fleet)-[:OPERATES]->(Vessel)` path triggers AGE's edge-uniqueness
   enforcement, which is missing in apache/age 1.6.0 / PG16.

### 5.3 Shared-node contract (the rule reviewers should enforce)

> OpsMap and OrgMap builders **MUST** `MATCH` existing `Crew`/`Vessel`/`Port` nodes and
> `MERGE` only edges/new-label nodes. Any `CREATE (:Crew …)` / `CREATE (:Vessel …)` in
> those builders is a bug — it would fork identity and violate the no-duplication exit
> criterion.

---

## 6. API interface

All under `/api/v1/graph` (router in [`L2Knowledge_graph/routes.py`](../routes.py)).
EntityMap/OrgMap endpoints return `503` when `GRAPH_BACKEND != age`.

**EntityMap (dimension 1):**

| Method & path | Purpose | Key params |
|---------------|---------|-----------|
| `GET /graph/summary` | EntityMap population (per-label node + per-type edge counts) | — |
| `GET /graph/facets` | Distinct ranks / certificates / ports for the UI filter dropdowns | — |
| `GET /graph/crew/search` | Crew search by rank + cert + port (any subset) | `rank`, `certificate`, `port`, `limit` |
| `GET /graph/crew/{crew_id}/traverse` | Full relationship traversal of one crew | `max_hops` (1–4) |
| `GET /graph/subgraph` | React-Flow-ready nodes+edges for a crew search (Query UI) | `rank`, `certificate`, `port`, `limit` |
| `GET /graph/node/{node_id}` | Full detail for one node (props + in/out relationships) | — |

**OrgMap (dimension 3) — live:**

| Method & path | Purpose | Key params |
|---------------|---------|-----------|
| `GET /graph/orgmap/summary` | OrgMap population (Company/Fleet/Rank + reused Vessel; OWNS/OPERATES/REQUIRES_RANK/HAS_RANK) | — |
| `GET /graph/orgmap/structure` | Company → Fleet → Vessel hierarchy as a React-Flow graph | — |
| `GET /graph/orgmap/manning-gap` | **Headline query**: required vs. have per rank, with the gap | `company`, `fleet`, `vessel` (any subset = scope) |

`crew/search`, `crew/{id}/traverse`, `subgraph` and `orgmap/structure` return an
`elapsed_ms` field for the < 3 s latency criterion. **OpsMap endpoints are also live**
under the same prefix
(`GET /graph/opsmap/{summary,process,reference,variants,bottlenecks,conformance,cases}`,
`POST /graph/opsmap/persist` — see [`OPSMAP_DESIGN.md`](OPSMAP_DESIGN.md) §5): both a
**discovered** model (`/opsmap/process`, mined from events) and a **reference/designed**
model (`/opsmap/reference`, always populated) are served.

### Python query layer

**EntityMap (`L2Knowledge_graph/entity_map.py`):**
- `build_entity_map()` — idempotent (re)build from the `crew` table.
- `entity_map_summary()` — counts.
- `search_crew(rank, certificate, port, limit)` — graph search.
- `traverse_crew(crew_id, max_hops)` — neighbourhood + multi-hop reach.

**OrgMap (`L2Knowledge_graph/org_map.py`):**
- `build_org_map()` — idempotent overlay build (MATCHes EntityMap Vessel/Crew, MERGEs the rest).
- `org_map_summary()` — per-label / per-type counts for the overlay.
- `org_structure()` — Company → Fleet → Vessel hierarchy as `{nodes, edges}`.
- `manning_gap(company, fleet, vessel)` — required vs. have vs. gap per rank for a scope.

---

## 7. AGE query patterns & test scenarios

### 7.1 Cypher patterns used

**Idempotent upsert** (every node):
```cypher
MERGE (c:Crew {crew_id:'SNO-1000'})
SET c.name='Juan dela Cruz', c.rank='Chief Officer', c.experience_years=12
```

**Multi-relationship search** (rank property + certificate/port traversal):
```cypher
MATCH (c:Crew)
MATCH (c)-[:HOLDS]->(:Certificate {type:'GMDSS'})
MATCH (c)-[:CURRENTLY_AT]->(:Port {name:'Rotterdam'})
WHERE c.rank = 'Chief Officer'
RETURN {crew_id:c.crew_id, name:c.name, rank:c.rank, port:c.port} AS v
```

**Multi-hop traversal** (variable length):
```cypher
MATCH path = (c:Crew {crew_id:'SOF-2000'})-[*1..2]->(n)
RETURN {hops: length(path), endpoint: coalesce(n.name, n.type, n.contract_id), endpoint_type: labels(n)[0]} AS v
```

> **Implementation gotchas captured during the build (so reviewers don't re-hit them):**
> 1. Queries are returned as **scalar maps** (`RETURN {…} AS v`), not raw vertices —
>    AGE annotates vertices/edges with a `::vertex` suffix that breaks `json.loads`.
> 2. `run_cypher` sends **raw SQL** (`exec_driver_sql`), never SQLAlchemy `text()` —
>    Cypher's `[:HOLDS]` / `(n:Crew)` colons collide with `:param` binding.
> 3. Each connection runs `LOAD 'age'` + sets `search_path`, and `run_cypher`
>    **commits** (cypher() can mutate; otherwise MERGE writes roll back).

### 7.2 Test scenarios (10 known queries with expected paths)

| # | Scenario | Query | Expected |
|---|----------|-------|----------|
| 1 | Rank only | `search?rank=Master` | 4 crew (Volkov, Kovalenko, Diakos, Kravchenko) |
| 2 | Certificate only | `search?certificate=GMDSS` | 7 crew hold GMDSS |
| 3 | Port only | `search?port=Mumbai` | 7 crew currently at Mumbai |
| 4 | rank + cert + port | `search?rank=Chief Officer&certificate=GMDSS&port=Rotterdam` | exactly 1 (Piotr Kowalski) |
| 5 | Empty result handled | `search?rank=Master&port=Manila` | 0, no error |
| 6 | Direct traversal | `crew/SNO-1000/traverse` | HOLDS×4 + CURRENTLY_AT Singapore |
| 7 | Multi-hop Crew→Vessel→Port | `crew/SOF-2000/traverse?max_hops=2` | reaches Singapore via ASSIGNED_TO→CALLS_AT |
| 8 | Contract path | `crew/SOF-2000/traverse` | SIGNED → CT-SOF-2000 (Contract) |
| 9 | Population check | `summary` | 90 nodes, 189 edges, 5 labels |
| 10 | No duplication | distinct Vessel / Port nodes | 5 / 9 (not 40) |
| 11 | OrgMap population | `orgmap/summary` | Company 2, Fleet 4, Rank 14; OWNS 4, OPERATES 5, REQUIRES_RANK 55, HAS_RANK 40 |
| 12 | OrgMap no duplication | Vessel nodes after overlay | still 5 (overlay added edges, not nodes) |
| 13 | Manning gap (company) | `orgmap/manning-gap?company=Oceanic Shipping Lines` | AB Seaman gap 5, Master gap 3, Bosun gap 3, … |
| 14 | Manning gap (vessel) | `orgmap/manning-gap?vessel=MV Pacific Star` | per-ship required vs. have for one vessel |

### 7.3 Latency benchmark (measured)

| Endpoint | Observed |
|----------|----------|
| `crew/search` (any filter combo) | **5–6 ms** |
| `crew/{id}/traverse` (2 hops) | **27–37 ms** |

All **≪ 3 s** budget.

---

## 8. How to build / run

```bash
# 1. AGE-enabled Postgres holding the crew table (docker container: crew-postgres)
#    image: apache/age:release_PG16_1.6.0  (postgres/password, db maritime_crew)
# 2. enable the backend
echo "GRAPH_BACKEND=age" >> backend/.env
# 3. seed relational crew, then the graph dimensions (order matters:
#    EntityMap owns the Crew/Vessel nodes the OrgMap overlay MATCHes)
cd backend
python -m scripts.seed_crew                          # 20 sign-on + 20 sign-off rows
python -m L2Knowledge_graph.scripts.seed_entity_map  # dimension 1: EntityMap
python -m L2Knowledge_graph.scripts.seed_org_map     # dimension 3: OrgMap overlay
# 4. query
curl 'localhost:8000/api/v1/graph/summary'
curl 'localhost:8000/api/v1/graph/crew/search?rank=Master'
curl 'localhost:8000/api/v1/graph/orgmap/summary'
curl 'localhost:8000/api/v1/graph/orgmap/manning-gap?company=Oceanic%20Shipping%20Lines'
```

---

## 9. Open items for Jun 12 review

1. **Vessel `CALLS_AT` fan-out:** because each onboard crew contributes its own port,
   a vessel can appear to call at several ports. Fine for entity context; OpsMap should
   own the *current* port-of-call as authoritative single-valued state.
2. **Cypher literal inlining:** values are escaped (`_q`) and inlined since AGE has no
   bind parameters. Acceptable for internal/trusted inputs; revisit if graph search is
   ever exposed to untrusted callers.
3. **All three dimensions are now built** — EntityMap, OpsMap (process-mining DFG,
   [`OPSMAP_DESIGN.md`](OPSMAP_DESIGN.md)) and OrgMap (§5.2). No dimension is outstanding.
4. **Manning template is uniform:** OrgMap applies one `MANNING` scale to every vessel
   (vessels aren't typed in the seed data). Per-vessel-type manning is a future refinement
   once vessel class is modelled.
5. **OrgMap `required` vs. `have` semantics:** `have` counts crew `ASSIGNED_TO` a vessel
   (onboard), so gaps reflect onboard headcount, not the sign-on candidate pool. Pairing
   the gap with OpsMap's sign-on candidates is the natural L3 next step.
6. **L1 → L2 ingestion contract** is now documented separately
   ([`L1_TO_L2_INGESTION_CONTRACT.md`](L1_TO_L2_INGESTION_CONTRACT.md),
   [`L1_TO_L2_ORGMAP_CONTRACT.md`](L1_TO_L2_ORGMAP_CONTRACT.md),
   [`L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`](L1_TO_L2_ENTITY_EVENT_TRIGGERS.md)) — how
   SignalFabric events drive incremental graph updates instead of full re-seeds.
