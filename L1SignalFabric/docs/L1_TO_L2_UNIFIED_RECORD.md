# L1 → L2 Unified Record — One Envelope for Every Map

**Audience:** the L1 (SignalFabric) and L2 (knowledge-graph) developers.
**Purpose:** define the **single record structure** L1 emits that feeds **all** L2
maps — OrgMap, EntityMap, OpsMap (and future maps) — for **all** connectors
(Slack, Gmail, Outlook, SharePoint, Notion, Database, ERP: Crew/Contract/Vessel).
**Implements:** [`l2/record.py`](../l2/record.py) — `L2Record`, the facet types, and
`project_record(SignalEvent) -> L2Record`.
**Supersedes nothing — unifies three contracts:** it is the transport envelope that
*carries* the per-map payloads already specified in
[`L1_TO_L2_INGESTION_CONTRACT.md`](../../L1_TO_L2_INGESTION_CONTRACT.md) (OpsMap) and
[`L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`](../../L1_TO_L2_ENTITY_EVENT_TRIGGERS.md) (EntityMap),
plus the OrgMap node/edge projection in [`l2/orgmap.py`](../l2/orgmap.py).

---

## 1. The model in one paragraph

Every source change in L1 is one canonical [`SignalEvent`](../core/signal.py). Each
`SignalEvent` projects into **one `L2Record` envelope** carrying **shared
provenance** (who/where/when) plus a list of **facets** — small typed
projections, one per map the record feeds. A single change can produce 0..N
facets (an ERP crew row → an **Org** node *and* an **Entity** `crew.upserted`; a
Slack sign-off notice → **Org** edge + **Org** sign-off + **Entity**
`crew.signed_off`; a relayed workflow step → **Ops** only). Each L2 map consumer
reads **only its own facet type** and ignores the rest — so the maps stay
decoupled while sharing one wire format and one idempotency key. **One envelope,
many facets, full records (never patches).**

---

## 2. Why one envelope instead of three feeds

The three maps want genuinely different shapes (Org = node/edge graph, Entity =
full-record MERGE, Ops = event-log row). Shipping three parallel streams means
three transports, three dedup schemes, three backfills, and N×3 producer
branches per connector. Folding them into one envelope gives:

- **One idempotency key.** `record_id` (= `SignalEvent.dedup_id`) dedups the
  whole envelope; each facet additionally carries its map's own MERGE key
  (`crew_id` / `case_id` / node id), so at-least-once redelivery is a no-op
  refresh in **every** map.
- **One transport + backfill.** Same endpoint/batch for a cold-start replay of
  all maps; consumers fan out by facet.
- **Additive evolution.** A new map = a new facet subclass + one producer branch;
  existing consumers ignore facets they don't recognise. The envelope never
  changes.

---

## 3. Envelope schema (`L2Record`)

| Field | Type | Req | Meaning |
|-------|------|:---:|---------|
| `record_id` | string | ✅ | `SignalEvent.dedup_id` — stable envelope MERGE key (idempotent redelivery). |
| `schema_version` | string | ✅ | `"l2-record/1.0"`. |
| `tenant_id` | string | ✅ | Multi-tenant scope. |
| `source_system` | enum | ✅ | `SLACK · EMAIL · GMAIL · OUTLOOK · NOTION · SHAREPOINT · DATABASE · CREW_DB · CONTRACT_CLM · VESSEL_PORT_DB`. |
| `connector` | string | — | Connector name (`slack`, `gmail`, `erp`, …). |
| `entity` | string | ✅ | Source-natural entity (`message`, `email`, `crew`, `contract`, `vessel_port`, …). |
| `key` | object | ✅ | Source-natural primary key. |
| `operation` | enum | ✅ | `DELTA · SNAPSHOT · DELETE`. `DELETE` drives `crew.deleted` / detach-delete. |
| `occurred_at` | ISO-8601 | ✅ | Source valid time — the timestamp every facet (esp. Ops) sorts by. |
| `ingested_at` | ISO-8601 | ✅ | L1 receive time (latency start). |
| `lineage` | object | — | Provenance passthrough (`extraction_id`, `source_endpoint`, `source_sequence`, `checksum`). |
| `raw` | object | — | Original `SignalEvent.data` for audit. Maps ignore keys they don't read. |
| `facets` | Facet[] | ✅ | The fan-out — 0..N typed map projections (§4). |

---

## 4. Facets — one per map (discriminated on `map`)

### 4.1 `OrgFacet` (`map: "org"`) → `OrgMap.upsert`
The existing tribal-knowledge node/edge projection — **unchanged** from what
`L2JsonlStore.project` returns today, so OrgMap consumes it as-is.

| Field | Type | Meaning |
|-------|------|---------|
| `kind` | enum | `node · edge · signoff_event`. |
| `label` | string | `POSTED_IN · REACTED_IN · MEMBER_OF · EMAILED · Crew · Vessel · SignOffEvent · …`. |
| `props` | object | Edge/node properties (participants, parsed crew subgraph hints). |
| `node_id` | string? | Business-key id for `node` / `signoff_event`. |

### 4.2 `EntityFacet` (`map: "entity"`) → `build_entity_map` / `_merge_crew`
The **full** entity record to MERGE — per
[`L1_TO_L2_ENTITY_EVENT_TRIGGERS.md`](../../L1_TO_L2_ENTITY_EVENT_TRIGGERS.md) §3.

| Field | Type | Meaning |
|-------|------|---------|
| `event` | enum | `crew.upserted · crew.signed_on · crew.signed_off · crew.deleted · contract.upserted · vessel.upserted · port.upserted`. |
| `record` | object | The complete current record (crew §3 fields, or contract/vessel/port natural fields). **Declarative, not a patch.** |

### 4.3 `OpsFacet` (`map: "ops"`) → `record_event`
One process-mining event-log row — per
[`L1_TO_L2_INGESTION_CONTRACT.md`](../../L1_TO_L2_INGESTION_CONTRACT.md) §2.
`timestamp` is the envelope `occurred_at` (not repeated).

| Field | Type | Meaning |
|-------|------|---------|
| `case_id` | string | = `workflow_id` — the case join key. |
| `event_type` | enum | §3 vocabulary (`workflow_created`, `agent_completed`, `crew_signed_on`, …). |
| `agent_name` | string? | **Required** when `event_type = "agent_completed"`. |
| `data` | object | Curated identity/outcome keys (§4 of the Ops contract). |

---

## 5. Connector → facet matrix (what `project_record` emits)

| Connector / entity | Org facet | Entity facet | Ops facet |
|--------------------|:---------:|:------------:|:---------:|
| **ERP** `crew` (CREW_DB) | `Crew` node + subgraph | `crew.upserted` / `crew.signed_off` (full §3 record) | — |
| **ERP** `contract` (CONTRACT_CLM) | `Contract` node | `contract.upserted` | — |
| **ERP** `vessel_port` (VESSEL_PORT_DB) | `Vessel`/`Port` node | `vessel.upserted` / `port.upserted` | — |
| **Slack** message (chatter) | `POSTED_IN` edge | — | — |
| **Slack** sign-on/off **notice** | edge + crew subgraph | `crew.signed_on/off` (parsed fields) | (if `workflow_id` present) |
| **Gmail/Outlook/Email** message | `EMAILED` edge | — | — |
| **Gmail/Outlook** sign-off (`l2Intent`) | `SignOffEvent` node | (parsed, if name found) | `workflow_created` (if `workflow_id`) |
| **Notion** page/db/block | `node` (document) | — | — |
| **SharePoint** drive/list item | `node` | — | — |
| **Database** outbox row | `node` | (if table is an entity) | — |
| **Backend workflow** step (relayed) | — | — | §3 event (uses `metadata.case_id` / `event_type` / `agent_name`) |

> The Org facet is **always** emitted (backward-compatible with today's sink).
> Entity/Ops facets are added when the record carries entity or workflow
> semantics — see [`l2/record.py`](../l2/record.py) `_entity_facet` / `_ops_facet`.

---

## 6. Worked examples

**ERP crew sign-off** (`CREW_DB`, `pool=signoff`) → Org node **and** Entity trigger:
```jsonc
{ "record_id": "6bc9de7a…", "schema_version": "l2-record/1.0", "tenant_id": "acme",
  "source_system": "CREW_DB", "connector": "erp", "entity": "crew",
  "key": { "crew_id": "SOF-2000" }, "operation": "DELTA",
  "occurred_at": "2026-06-09T08:00:00Z", "ingested_at": "2026-06-09T08:00:01Z",
  "facets": [
    { "map": "org", "kind": "node", "label": "Crew",
      "node_id": "node:6bc9de7a…", "props": { "crew_id": "SOF-2000", "name": "Juan dela Cruz", … } },
    { "map": "entity", "event": "crew.signed_off",
      "record": { "crew_id": "SOF-2000", "pool": "signoff", "name": "Juan dela Cruz",
                  "rank": "Chief Officer", "vessel": "MV Pacific Star", "port": "Singapore",
                  "joining_date": "2025-11-03", "certifications": ["GMDSS","STCW II/2"],
                  "experience_years": 14 } } ] }
```

**Gmail sign-off carrying a workflow id** → Org `SignOffEvent` **and** Ops event:
```jsonc
{ "record_id": "78658348…", "source_system": "GMAIL", "connector": "gmail", "entity": "email",
  "key": { "id": "m1" }, "operation": "DELTA", "occurred_at": "2026-06-09T08:02:30Z",
  "facets": [
    { "map": "org", "kind": "signoff_event", "label": "SignOffEvent",
      "node_id": "signoff:78658348…", "props": { "subject": "Sign off …", "from": "a@x.com" } },
    { "map": "ops", "case_id": "wf-1042", "event_type": "workflow_created", "data": {} } ] }
```

**Slack chatter** → Org edge only:
```jsonc
{ "record_id": "29046bed…", "source_system": "SLACK", "entity": "message",
  "facets": [ { "map": "org", "kind": "edge", "label": "POSTED_IN",
                "props": { "user": "bob", "channel": "general", "channel_id": "general" } } ] }
```

---

## 7. Transport (one endpoint, fan-out by facet)

**Proposed:** `POST /api/v1/graph/records` · `Content-Type: application/json` —
single record or batch (cold-start backfill):

```jsonc
{ "record": { …L2Record… } }
// or
{ "records": [ { …L2Record… }, … ] }
```

L2 splits each record's `facets` and routes them: `org` → `OrgMap.upsert`,
`entity` → `build_entity_map`/`_merge_*`, `ops` → `record_event`. The three
existing per-map endpoints remain valid as direct ingress; this envelope is the
**unified** path that feeds them all from one stream.

**Response (200):** `{ "received": 20, "org": 20, "entity": 14, "ops": 6 }`
(per-facet counts).

---

## 8. Checklist for the L1 developer

- [ ] Emit one `L2Record` per `SignalEvent` via `project_record` (Org facet always).
- [ ] Carry the **full** entity record in Entity facets (declarative — drives MERGE/HOLDS).
- [ ] Set `operation = "DELETE"` for removals so Entity can `crew.deleted`.
- [ ] For relayed workflow steps, set `metadata.case_id` (= `workflow_id`),
      `event_type`, and `agent_name` (for `agent_completed`) so an Ops facet is built.
- [ ] **Normalise vessel / port / certificate / channel names** — they are graph
      join keys across Org *and* Entity facets.
- [ ] Keep `occurred_at` accurate — Ops sorts the case timeline by it.
