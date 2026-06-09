# L1 → L2 Ingestion Contract — Raw Event Schema

**Audience:** the developer building the **L1 (SignalFabric)** layer.
**Purpose:** the exact shape of the raw event records L1 must deliver to L2 so the OpsMap
process-mining dimension can build the crew-change process model. This is a **producer
contract** — it tells L1 what to send, what each field means, and which fields L2 reads.
**Consumer:** `record_event()` in [`ops_map.py`](../ops_map.py), surfaced under
`/api/v1/graph/opsmap/...`.
**Status:** proposed — review with L2 before implementing.

---

## 1. What L2 does with each record (why shape matters)

L2 OpsMap turns your events into a **per-case event log**, keyed by a **case id**. It groups
by `case_id`, sorts by `timestamp`, maps each `event_type` to a canonical **activity**, then
mines the directly-follows graph, variants, bottlenecks and conformance. Three things are
therefore critical from L1: a **stable `case_id`**, a **recognised `event_type`**, and an
**accurate `timestamp`**. Everything else is detail payload L2 selectively keeps.

---

## 2. Event record schema

| Field | Type | Required | Meaning |
|-------|------|:--------:|---------|
| `case_id` | string | **yes** | The crew-change this event belongs to. Use the **`workflow_id`** — identical across every event of the case, from initiation to terminal. This is the join key; if it varies, L2 sees unrelated cases. |
| `event_type` | string (enum) | **yes** | What happened — one of the §3 values. Unknown values are accepted but silently ignored (they won't appear in the process model). |
| `agent_name` | string | conditional | **Required when `event_type = "agent_completed"`** — names which specialist finished; it's how L2 resolves the activity. Otherwise optional (stored as the step `actor`, default `"Master Agent"`). |
| `timestamp` | string (ISO-8601) | **send always** | When it occurred: `2026-06-09T14:32:05.120Z` or `...+00:00`. If missing/unparseable, L2 stamps its own receipt time — **which corrupts ordering and durations**. |
| `data` | object | optional | The raw business payload. L2 keeps only the §4 keys; everything else is dropped (harmless). |

Rules: `case_id` + `event_type` are the only hard-required fields. L2 **collapses immediate
duplicate activities** within a case (a specialist emitting several `agent_completed` →
one step), so you needn't de-duplicate — but don't rely on duplicates being counted.

---

## 3. `event_type` vocabulary (what L2 recognises)

Anything **not** in this table is ignored — so it is safe but pointless to send stream noise
(`agent_message`, `agent_thinking`, `model_usage`, `agent_tool_use`, `master_routing`,
`master_waiting`).

| `event_type` | `agent_name`? | Activity | Terminal |
|--------------|:-------------:|----------|:--------:|
| `workflow_created` | — | Sign-Off Initiated | |
| `agent_completed` | **yes** | by `agent_name` (below) | |
| `crew_updated` | — | Sign-Off Confirmed | |
| `auto_compliance` | — | Compliance Check | |
| `sign_on_initiated` | — | Compliance Check *(manual sign-on entry)* | |
| `crew_signed_on` | — | Signed On | ✅ success |
| `sign_on_rejected` | — | Sign-On Rejected | ✅ compliance failure |
| `workflow_failed` | — | Workflow Failed | ✅ error |

**`agent_completed` → activity (by `agent_name`):**

| `agent_name` | Activity |
|--------------|----------|
| `Crew Matching Agent` | Crew Matching |
| `Travel Agent` | Travel Arranged |
| `Notification Agent` | Crew Notified |
| `Compliance Agent` | Compliance Check |

An `agent_completed` with any other (or missing) `agent_name` is ignored. The first three run
**in parallel** in Phase 1 — emit each when it finishes; their relative order may vary
between cases.

**Healthy-case lifecycle:**
```
workflow_created
  → agent_completed (Crew Matching Agent)   ┐ parallel, any order
  → agent_completed (Travel Agent)          │
  → agent_completed (Notification Agent)    ┘
  → crew_updated
  → auto_compliance        (or sign_on_initiated for the manual path)
  → crew_signed_on         (terminal: success)
```
Exception terminals replace the last step: `sign_on_rejected` (compliance failure) or
`workflow_failed` (error).

---

## 4. The `data` payload — fields L2 keeps

L2 curates `data` down to identity/outcome fields so per-case detail views can answer *"whose
case is this, and how did it end"*. Send these keys where they apply (on the event where the
value becomes known); anything else in `data` is dropped (bulky/derived blobs like a full
compliance subgraph are intentionally not kept).

| Key | Type | Send on | Used for |
|-----|------|---------|----------|
| `crew_name` | string | `workflow_created` (signing-off), terminal (signing-on) | who signed off / on |
| `rank` | string | `workflow_created` | signing-off crew rank |
| `vessel` | string | `workflow_created` | vessel of the change |
| `crew_id` | string | any | crew key |
| `candidate_name` | string | compliance events | proposed sign-on candidate |
| `candidate_rank` | string | compliance events | candidate rank |
| `candidate_id` | string | compliance events | candidate key |
| `crew_rank` | string | any | alt rank field |
| `compliance_status` | string | `crew_signed_on` / `sign_on_rejected` | pass/fail per case |
| `compliance_score` | number | `crew_signed_on` / `sign_on_rejected` | numeric score |
| `status` | string | any | generic status |
| `pool` | string | any | crew pool |
| `error` | string | `workflow_failed` | failure reason (drives the case `reason`) |
| `failures` | string[] | `sign_on_rejected` | rejection reasons (joined into `reason`) |
| `recommendation` | string | compliance events | recommendation text |
| `message` | string | `sign_on_rejected` (fallback reason), any | human-readable note |

> **Rule of thumb:** signing-off identity (`crew_name`, `rank`, `vessel`) on
> `workflow_created`; outcome (`compliance_status`, `compliance_score`, `failures` / `error`)
> on the **terminal** event.

---

## 5. Transport — how L1 sends events to L2

> **Open decision for L1/L2 to agree on.** Today the hook is in-process
> (`WorkflowService._event_callback` → `ops_map.record_event(...)`). For a wire-separated L1,
> the proposed contract is a single HTTP endpoint. Confirm path/auth with L2 before building.

**Proposed:** `POST /api/v1/graph/opsmap/events` · `Content-Type: application/json`

Accept **either** a single event **or** a batch (preferred for SignalFabric streaming):

```jsonc
// single
{ "case_id": "...", "event_type": "...", "timestamp": "...", "data": { ... } }

// batch — preferred for streaming
{ "events": [ { ...event... }, { ...event... } ] }
```

**Proposed response (200):**
```jsonc
{ "received": 5, "recorded": 4, "ignored": 1 }
```
`ignored > 0` is **normal** (recognised-but-noise / unmapped events), not a failure.

**Delivery expectations for L1:**
- Events may arrive **out of order** within a case — L2 re-sorts by `timestamp`, so a correct
  timestamp matters more than send order.
- At-least-once delivery is fine: consecutive duplicate activities per case are collapsed.
- One `case_id` may span multiple requests/batches over time — do not assume a case completes
  in a single flush.

---

## 6. Worked examples

A complete happy-path case (`case_id = "wf-1042"`):

```jsonc
{ "case_id": "wf-1042", "event_type": "workflow_created", "timestamp": "2026-06-09T08:00:00Z",
  "data": { "crew_name": "Juan dela Cruz", "rank": "Chief Officer", "vessel": "MV Pacific Star", "crew_id": "SOF-2000" } }

{ "case_id": "wf-1042", "event_type": "agent_completed", "agent_name": "Crew Matching Agent", "timestamp": "2026-06-09T08:00:12Z",
  "data": { "candidate_name": "Piotr Kowalski", "candidate_rank": "Chief Officer", "candidate_id": "SNO-1000" } }

{ "case_id": "wf-1042", "event_type": "agent_completed", "agent_name": "Travel Agent", "timestamp": "2026-06-09T08:00:15Z", "data": {} }
{ "case_id": "wf-1042", "event_type": "agent_completed", "agent_name": "Notification Agent", "timestamp": "2026-06-09T08:00:18Z", "data": {} }
{ "case_id": "wf-1042", "event_type": "crew_updated", "timestamp": "2026-06-09T08:01:00Z", "data": {} }

{ "case_id": "wf-1042", "event_type": "auto_compliance", "timestamp": "2026-06-09T08:02:30Z",
  "data": { "candidate_name": "Piotr Kowalski", "compliance_score": 0.97 } }

{ "case_id": "wf-1042", "event_type": "crew_signed_on", "timestamp": "2026-06-09T08:02:45Z",
  "data": { "crew_name": "Piotr Kowalski", "compliance_status": "pass", "compliance_score": 0.97 } }
```

A rejection terminal (replaces the final event):
```jsonc
{ "case_id": "wf-1043", "event_type": "sign_on_rejected", "timestamp": "2026-06-09T09:14:00Z",
  "data": { "crew_name": "A. Candidate", "compliance_status": "fail", "compliance_score": 0.41,
            "failures": ["GMDSS certificate expired", "Medical not valid for vessel type"] } }
```

A failure terminal:
```jsonc
{ "case_id": "wf-1044", "event_type": "workflow_failed", "timestamp": "2026-06-09T10:05:00Z",
  "data": { "error": "Travel booking provider timed out after 3 retries" } }
```

---

## 7. Checklist for the L1 developer

- [ ] Every event carries a **stable `case_id`** (= `workflow_id`) for the whole crew-change.
- [ ] `event_type` is one of the §3 values; `agent_name` is set for **every** `agent_completed`.
- [ ] Every event carries an accurate **ISO-8601 `timestamp`** (UTC or with offset).
- [ ] Identity payload on `workflow_created`; outcome payload (`compliance_status`,
      `compliance_score`, `failures` / `error`) on the **terminal** event.
- [ ] Only the §3 event types are sent (others are dropped).
- [ ] Transport agreed with L2 (§5): in-process call vs. `POST /opsmap/events` single/batch.