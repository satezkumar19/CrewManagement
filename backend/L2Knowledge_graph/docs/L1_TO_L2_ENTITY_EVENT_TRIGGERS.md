# L1 → L2 Entity Event Triggers — Raw Record Schema

**Audience:** the developer building the **L1 (SignalFabric)** layer.
**Purpose:** the **event triggers** L1 fires whenever raw crew records land or change, and the
**record schema** each trigger carries, so L2 can build/refresh the **EntityMap** (the canonical
Crew / Vessel / Port / Certificate / Contract graph). This is the **data-change** counterpart to
[`L1_TO_L2_INGESTION_CONTRACT.md`](L1_TO_L2_INGESTION_CONTRACT.md) — that doc covers *runtime
workflow events* (OpsMap, process mining); **this** doc covers *the underlying entity data*.
**Consumer:** `build_entity_map()` / `_merge_crew()` / `_merge_contract()` in
[`entity_map.py`](../entity_map.py).
**Status:** proposed — review with L2 before implementing.

---

## 1. The model in one paragraph

EntityMap is built **per crew record**. Each record L1 lands becomes one `(:Crew)` node, and
L2 derives that crew's edges from the record's own fields — its certificates, current port,
assigned vessel, and (for onboard crew) an engagement Contract. L2 is **idempotent**: every
node/edge is `MERGE`d on a business key, so re-sending the same record refreshes properties and
never duplicates. **That means the trigger contract is simple: send the full record on every
change, and L2 reconciles.** You do not need to compute graph deltas — L1 emits record-level
facts, L2 owns the graph shape.

---

## 2. The event triggers

L1 fires one of these whenever the underlying crew data changes. Each carries the **full crew
record** (§3) — not a partial patch — because L2 re-MERGEs the whole record.

| Trigger (`event`) | Fires when | L2 effect |
|-------------------|-----------|-----------|
| `crew.upserted` | A crew record is **created or updated** in L1 (new candidate landed, details edited, certificates changed, port/vessel changed) | MERGE the `Crew` node + re-derive all its edges (§4). The single workhorse trigger. |
| `crew.signed_off` | An onboard seafarer **signs off** a vessel (enters the sign-off pool) | Same as upsert with `pool = "signoff"` → also creates the engagement `Contract` (§4). |
| `crew.signed_on` | A candidate **signs on** to a vessel (assigned a ship) | Upsert with the now-populated `vessel`/`port` → adds `ASSIGNED_TO` / `CALLS_AT` and the Contract. |
| `crew.deleted` | A crew record is removed in L1 | Detach-delete the `Crew` node and its edges. *(Not yet implemented on L2 — confirm before relying on it.)* |

> **Minimum viable contract:** if L1 can only emit one trigger, emit **`crew.upserted` on every
> create/update** with the full record and a correct `pool` field. `signed_off` / `signed_on`
> are semantic conveniences — L2 derives the same graph from an upsert whose `pool`/`vessel`
> already reflect the new state.

### Why "full record, not a patch"
L2 rebuilds a crew's edges from the record's fields each time (e.g. it re-reads
`certifications[]` to MERGE `HOLDS` edges). A partial patch (`{crew_id, port}`) would let L2
refresh the port but it has no way to know the certificate list is unchanged vs. emptied. **Always
send the complete current record.**

---

## 3. Crew record schema (the payload every trigger carries)

| Field | Type | Required | Drives | Notes |
|-------|------|:--------:|--------|-------|
| `crew_id` | string | **yes** | `Crew` business key | Stable unique id; the MERGE key. |
| `pool` | enum `signon` \| `signoff` | **yes** | candidate vs onboard | Determines whether a `Contract` is created (sign-off pool only). |
| `name` | string | yes | `Crew.name` | |
| `rank` | string | yes | `Crew.rank`, `Contract.rank` | e.g. `Master`, `Chief Officer`. Powers rank search. |
| `grade` | string | no | `Crew.grade` | |
| `nationality` | string | no | `Crew.nationality` | |
| `port` | string | conditional | `Port` node + `CURRENTLY_AT` edge; `Vessel CALLS_AT Port`; `Contract.port` / `AT_PORT` | The crew's **current** location. Send whenever known. |
| `vessel` | string | conditional | `Vessel` node + `ASSIGNED_TO` edge; `Contract` | The **assigned** ship. Use the literal **`"Available"`** (or omit) for an unassigned sign-on candidate — L2 treats `"Available"` as "no vessel" and creates no Vessel/Contract. |
| `status` | string | no | `Crew.status` | |
| `experience_years` | integer | no | `Crew.experience_years` | Defaults to `0` if absent; send a number, not a string. |
| `certifications` | string[] | no | `Certificate` nodes + `HOLDS` edges | One entry per certificate **type** (e.g. `"GMDSS"`, `"STCW II/2"`). The list is the source of truth for the crew's `HOLDS` edges — send the **complete** current set. |
| `joining_date` | string (date) | conditional | `Contract.start_date` | Required for `pool = "signoff"` (the engagement start). ISO date, e.g. `2026-05-20`. |

### Field rules
- `crew_id` and `pool` are the only hard-required fields; `name`/`rank` are strongly expected
  (rank powers the headline search).
- `vessel = "Available"` is a **sentinel**, not a real ship — it is how L1 says "candidate not
  yet assigned". Don't invent placeholder Vessel names.
- `certifications` is **declarative**: whatever you send becomes the crew's full `HOLDS` set on
  the next upsert. To remove a certificate, send the list without it.

---

## 4. What L2 derives from one record (the trigger → graph mapping)

For a record with `crew_id`, `port`, `vessel`, `certifications`, `pool`:

```
(:Crew {crew_id, name, rank, grade, nationality, port, vessel, status, pool, experience_years})

  ── HOLDS ─────────► (:Certificate {type})        one per certifications[] entry
  ── CURRENTLY_AT ──► (:Port {name})               if port present
  ── ASSIGNED_TO ───► (:Vessel {name})             if vessel present and ≠ "Available"
                          └─ CALLS_AT ─► (:Port)   vessel → the crew's port (onboard only)

  pool == "signoff" only — the engagement:
  ── SIGNED ────────► (:Contract {contract_id = "CT-<crew_id>", rank, vessel, port, start_date, status:"Active"})
                          ├─ FOR_VESSEL ─► (:Vessel {name})
                          └─ AT_PORT ─────► (:Port {name})
```

Node identities (MERGE keys): `Crew.crew_id`, `Vessel.name`, `Port.name`,
`Certificate.type`, `Contract.contract_id`. Because every dimension MERGEs on these keys, a
vessel named `MV Pacific Star` is **one** node no matter how many crew reference it — so L1
must send **consistent spellings** of vessel/port/certificate names (they are the join keys).

> ⚠️ **Naming consistency is the one thing L1 must get right.** `"Rotterdam"` and
> `"Rotterdam "` (trailing space), or `"MV Pacific Star"` vs `"Pacific Star"`, become **two
> different nodes**. Normalise names at the L1 source.

---

## 5. Transport (decide with L2)

> Today EntityMap is built by a batch read of the crew table (`build_entity_map()` reads the
> `signon`/`signoff` pools and MERGEs each record). For an event-driven L1, the proposed
> contract is a single endpoint mirroring the per-record MERGE.

**Proposed:** `POST /api/v1/graph/entities/crew` · `Content-Type: application/json`

Accept a single record **or** a batch (for an initial backfill / bulk landing):

```jsonc
// single change
{ "event": "crew.upserted", "record": { ...crew record (§3)... } }

// batch — initial load or bulk flush
{ "events": [ { "event": "crew.upserted", "record": { ... } }, ... ] }
```

**Proposed response (200):** `{ "received": 20, "merged": 20, "nodes": 90, "edges": 189 }`
(post-merge population, mirroring `entity_map_summary()`).

**Expectations for L1:**
- **Idempotent / at-least-once** is fine — re-sending a record is a no-op refresh.
- Send the **full record** per change (§2), not a delta.
- For a cold start, send every current record once (the batch form) to populate the graph.

---

## 6. Worked examples

A sign-on candidate landing (unassigned — no vessel/contract):
```jsonc
{ "event": "crew.upserted",
  "record": {
    "crew_id": "SNO-1000", "pool": "signon",
    "name": "Piotr Kowalski", "rank": "Chief Officer", "grade": "Senior",
    "nationality": "Poland", "port": "Rotterdam", "vessel": "Available",
    "status": "Available", "experience_years": 12,
    "certifications": ["GMDSS", "STCW II/2", "Medical First Aid"]
  } }
```

An onboard seafarer in the sign-off pool (gets a Contract):
```jsonc
{ "event": "crew.signed_off",
  "record": {
    "crew_id": "SOF-2000", "pool": "signoff",
    "name": "Juan dela Cruz", "rank": "Chief Officer", "grade": "Senior",
    "nationality": "Philippines", "port": "Singapore", "vessel": "MV Pacific Star",
    "status": "Onboard", "experience_years": 14,
    "certifications": ["GMDSS", "STCW II/2"],
    "joining_date": "2025-11-03"
  } }
```
→ L2 creates `Crew SOF-2000`, `HOLDS` GMDSS/STCW, `CURRENTLY_AT` Singapore, `ASSIGNED_TO`
MV Pacific Star (which `CALLS_AT` Singapore), and `Contract CT-SOF-2000` (`SIGNED`,
`FOR_VESSEL` → MV Pacific Star, `AT_PORT` → Singapore).

---

## 7. Checklist for the L1 developer

- [ ] Fire `crew.upserted` on **every** create/update; carry the **full** record (§3).
- [ ] Always set `crew_id` and `pool`; use `vessel = "Available"` for unassigned candidates.
- [ ] Send `certifications` as the **complete** current list (it's declarative — drives `HOLDS`).
- [ ] Include `joining_date` for `pool = "signoff"` (the Contract start date).
- [ ] **Normalise vessel / port / certificate names** — they are graph join keys (§4).
- [ ] Transport agreed with L2 (§5): batch table read vs. `POST /entities/crew` single/batch.