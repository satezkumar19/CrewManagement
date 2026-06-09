# L1 → L2 OrgMap Contract — Org Structure & Manning Schema

**Audience:** the developer building the **L1 (SignalFabric)** layer.
**Purpose:** the shape of the **organizational structure** (Company → Fleet → Vessel ownership)
and the **manning template** (required headcount per rank) that L1 must deliver so L2 can build
the **OrgMap** dimension — the hierarchy *above* the vessel, overlaid on the existing EntityMap
graph. This is the **third** L1 producer contract, alongside
[`L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`](L1_TO_L2_ENTITY_EVENT_TRIGGERS.md) (the entity data) and
[`L1_TO_L2_INGESTION_CONTRACT.md`](L1_TO_L2_INGESTION_CONTRACT.md) (the runtime workflow events).
**Consumer:** `build_org_map()` in [`org_map.py`](../org_map.py), surfaced under
`/api/v1/graph/orgmap/...`.
**Status:** proposed — review with L2 before implementing. Today this data is **authored
statically** in [`org_data.py`](../org_data.py) (`ORG_TREE`, `MANNING`); this contract is how L1
replaces that hardcode with real source data.

---

## 1. The model in one paragraph

OrgMap is an **overlay**, not a fresh graph. It MATCHes the `Vessel` and `Crew` nodes EntityMap
already built and adds only the structure above them: three new node labels — `Company`, `Fleet`,
`Rank` — and four edges. It **never creates Crew or Vessel** (that's EntityMap's job — §5.3 of the
L2 design), so the one thing L1 must get right is that **vessel and rank names match what the
entity contract already sent** (they are the join keys). L2 is **idempotent**: every node/edge is
`MERGE`d on a business key, so re-sending the same structure refreshes and never duplicates.

```
(:Company)─[:OWNS]──────►(:Fleet)─[:OPERATES]─►(:Vessel)     ownership hierarchy
(:Vessel)─[:REQUIRES_RANK {required}]─►(:Rank)               manning requirement per ship
(:Crew)──[:HAS_RANK]───►(:Rank)                              crew rank → shared Rank node
```

Node identities (MERGE keys): `Company.name`, `Fleet.name`, `Rank.name`, plus the **reused**
`Vessel.name` and `Crew.crew_id`.

> **`HAS_RANK` needs no submission from this contract.** L2 derives `(:Crew)-[:HAS_RANK]->(:Rank)`
> from the **`rank` field on each crew record** you already send via the entity contract — it's not
> part of the OrgMap payload. Just keep crew `rank` spellings consistent with the manning `rank`
> names (§4); that consistency is the join that makes the manning-gap query work.

---

## 2. What L2 does with the data (why shape matters)

L2 consumes two things from L1 here:

1. **The ownership tree** → `Company`, `Fleet` nodes and the `OWNS` / `OPERATES` edges down to the
   *existing* vessels. This powers the org-hierarchy view (`/orgmap/structure`) and defines the
   **scopes** (a company, a fleet, a vessel) the headline query rolls up over.
2. **The manning template** → `(:Vessel)-[:REQUIRES_RANK {required}]->(:Rank)`. This is the
   "should have" side of the headline query.

**Headline query** (`/orgmap/manning-gap`): for a scope, `required` = Σ `REQUIRES_RANK.required`
over the scope's vessels, `have` = count of crew `ASSIGNED_TO` those vessels by rank, and
`gap = required − have`. So the manning numbers you send become the staffing target every gap is
measured against.

---

## 3. The two payloads

### 3a. Org structure record (ownership tree)

One record per **company**, carrying its full fleet→vessel tree. Send the whole company subtree on
every change (declarative, like the entity contract — L2 re-MERGEs the structure).

| Field | Type | Required | Drives | Notes |
|-------|------|:--------:|--------|-------|
| `company` | string | **yes** | `Company` node (MERGE key) | The owning company. |
| `fleets` | array | **yes** | `Fleet` nodes + `OWNS` / `OPERATES` edges | One entry per fleet (below). |
| `fleets[].fleet` | string | **yes** | `Fleet` node (MERGE key) + `OWNS` from company | Fleet name. |
| `fleets[].vessels` | string[] | **yes** | `OPERATES` → existing `Vessel` | Vessel names — **must match EntityMap `Vessel.name` exactly** (§4). A vessel L2 can't MATCH is silently skipped (no `OPERATES` edge). |

### 3b. Manning record (required headcount per rank)

The "standard manning scale" — required headcount per rank. Today one template applies to every
vessel ([`org_data.MANNING`](../org_data.py)); this contract lets L1 send **per-vessel** manning
(different ship types man differently) with the template as the fallback.

| Field | Type | Required | Drives | Notes |
|-------|------|:--------:|--------|-------|
| `vessel` | string | conditional | scopes the `REQUIRES_RANK` edges to one ship | The vessel this manning applies to — must match `Vessel.name`. **Omit** (or set `"default": true`) to send the org-wide template applied to every vessel. |
| `manning` | array | **yes** | `(:Vessel)-[:REQUIRES_RANK {required}]->(:Rank)` | One entry per rank. |
| `manning[].rank` | string | **yes** | `Rank` node (MERGE key) + `REQUIRES_RANK` | Rank name — **must match crew `rank`** so `required` and `have` line up (§4). |
| `manning[].required` | integer | **yes** | `REQUIRES_RANK.required` | Headcount required of that rank on that vessel. Send a **number** ≥ 0, not a string. |

> **`required`, not `count`.** L2 stores the headcount on the edge property `required` — `count`
> collides with Cypher's `COUNT()` in this AGE build. Send the field as `required` per the table.

---

## 4. Naming consistency — the one thing L1 must get right

OrgMap is pure join-by-name onto EntityMap. Two name spaces must agree, or edges silently fail to
attach (the data isn't rejected — it just doesn't connect, which is worse):

- **Vessel names** (`fleets[].vessels[]`, manning `vessel`) **must equal** the `vessel` values the
  entity contract sent (`Vessel.name`). `"MV Pacific Star"` ≠ `"Pacific Star"` ≠ `"MV Pacific Star "`
  (trailing space). If OrgMap can't MATCH the vessel, no `OPERATES` / `REQUIRES_RANK` edge is made.
- **Rank names** (`manning[].rank`) **must equal** the crew `rank` values (which drive `HAS_RANK`
  and the `have` count). If manning says `"Chief Officer"` but crew records say `"Ch. Officer"`,
  the gap query sees `required` against one Rank node and `have` against another → bogus gaps.

Normalise both at the L1 source. These are graph join keys, exactly as in
[`L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`](L1_TO_L2_ENTITY_EVENT_TRIGGERS.md) §4.

---

## 5. Transport (decide with L2)

> Today OrgMap is built from the static `org_data.py` tables by `build_org_map()`. For an
> L1-fed OrgMap, the proposed contract is two endpoints mirroring the per-record MERGE — slow-moving
> **reference/config data**, not a high-rate event stream (companies, fleets and manning scales
> change rarely).

**Proposed:**
- `POST /api/v1/graph/orgmap/structure` · org-structure record(s) (§3a)
- `POST /api/v1/graph/orgmap/manning` · manning record(s) (§3b)

Each accepts a single record **or** a batch:

```jsonc
// single
{ ...record... }

// batch — preferred for the initial load
{ "records": [ { ...record... }, ... ] }
```

**Proposed response (200):** the post-merge summary, mirroring `org_map_summary()`:
```jsonc
{ "received": 2, "merged": 2, "nodes": { "Company": 2, "Fleet": 4, "Rank": 11, "Vessel": 5 },
  "edges": { "OWNS": 4, "OPERATES": 5, "REQUIRES_RANK": 55, "HAS_RANK": 40 } }
```

**Expectations for L1:**
- **Idempotent / at-least-once** is fine — re-sending a company subtree or manning record is a no-op
  refresh.
- Send the **full company subtree** per structure change (not a per-vessel delta), so L2 re-MERGEs
  the whole tree.
- **Order:** seed EntityMap first (OrgMap MATCHes its Vessel/Crew). Send `structure` before
  `manning` isn't required (manning MERGEs the Vessel match independently), but vessels referenced by
  either must already exist as EntityMap `Vessel` nodes.

---

## 6. Worked examples

The current authored structure ([`org_data.py`](../org_data.py)), expressed as the L1 payload —
2 companies, 4 fleets, 5 vessels:

```jsonc
// POST /orgmap/structure  (batch)
{ "records": [
  { "company": "Oceanic Shipping Lines",
    "fleets": [
      { "fleet": "Pacific Fleet",  "vessels": ["MV Pacific Star", "MV Indian Ocean Pride"] },
      { "fleet": "Atlantic Fleet", "vessels": ["MV Atlantic Voyager"] }
    ] },
  { "company": "Meridian Maritime",
    "fleets": [
      { "fleet": "Tanker Division",      "vessels": ["MT Crude Titan"] },
      { "fleet": "Mediterranean Fleet",  "vessels": ["MV Mediterranean Queen"] }
    ] }
] }
```

The standard manning scale as the org-wide default (applied to every vessel):

```jsonc
// POST /orgmap/manning
{ "default": true,
  "manning": [
    { "rank": "Master",         "required": 1 },
    { "rank": "Chief Officer",  "required": 1 },
    { "rank": "Second Officer", "required": 1 },
    { "rank": "Third Officer",  "required": 1 },
    { "rank": "Chief Engineer", "required": 1 },
    { "rank": "Second Engineer","required": 1 },
    { "rank": "Third Engineer", "required": 1 },
    { "rank": "Bosun",          "required": 1 },
    { "rank": "AB Seaman",      "required": 2 },
    { "rank": "Electrician",    "required": 1 },
    { "rank": "Cook",           "required": 1 }
  ] }
```

A per-vessel override (a tanker needs an extra engineer and a Pumpman):

```jsonc
// POST /orgmap/manning
{ "vessel": "MT Crude Titan",
  "manning": [
    { "rank": "Master",          "required": 1 },
    { "rank": "Chief Officer",   "required": 1 },
    { "rank": "Chief Engineer",  "required": 1 },
    { "rank": "Second Engineer", "required": 2 },
    { "rank": "Pumpman",         "required": 1 },
    { "rank": "AB Seaman",       "required": 3 }
  ] }
```

---

## 7. Checklist for the L1 developer

- [ ] Seed/feed **EntityMap first** — OrgMap MATCHes its `Vessel` / `Crew` nodes (it creates neither).
- [ ] Send the **full company subtree** (`company` → `fleets[]` → `vessels[]`) on every structure change.
- [ ] **Vessel names match EntityMap `Vessel.name` exactly** — they are the `OPERATES` / manning join key (§4).
- [ ] **Manning `rank` names match crew `rank`** — that's the join between `required` and `have` (§4).
- [ ] Send `manning[].required` as a **number ≥ 0**; use `"default": true` (no `vessel`) for the org-wide template, or `vessel` for a per-ship override.
- [ ] Do **not** send `HAS_RANK` / crew↔rank — L2 derives it from the crew record's `rank` (entity contract).
- [ ] Transport agreed with L2 (§5): static `org_data.py` tables vs. `POST /orgmap/structure` + `/orgmap/manning`.
```
