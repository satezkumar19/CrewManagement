# L1 SignalFabric — Parallel Build & Daily Demo Plan

**Layer:** L1 SignalFabric
**Engineers:** Sreekumar M K, Sruthy
**Window:** Jun 08 → Jun 12, 2026 (daily demoable build)
**Milestones:** Prototype **Jun 10** · Design + Test docs (async review) **Jun 12** · Prod target **Jun 15**
**Source folder:** `L1SignalFabric/` (new top-level module)

---

## 1. Context & Goal

L1 SignalFabric is the **continuous event-ingestion layer** that feeds the L2 knowledge graph
(OrgMap + workflow nodes such as **SignOffEvent**). It extends the existing **upstream**
ingestion pattern from *batch snapshots* to *continuous streams*.

The upstream pipeline today ships file/batch adapters: `SourceAdapter → FileExtractor` (Phase 1), with
`APIExtractor` (Phase 2) and `CDCExtractor` (Phase 3) **stubbed for the future**. The Python
scrapers (Slack, Notion) pull history in batches and emit `*.jsonl` + `manifest.json`.
**L1 SignalFabric realizes the Phase-3 event-stream vision**: real-time push connectors
that emit the same canonical `Record` shape as continuous `DELTA` operations instead of
`SNAPSHOT` files.

### 1.1 Focus order (per request)

We build the three highest-value streams first. They map onto **all five** prototype sources:

| Focus area (build first) | Upstream source(s) covered | Feeds | Operation |
|---|---|---|---|
| **1. Slack Events API** — real-time webhook push (messages, reactions, channel joins) | Slack | OrgMap *tribal knowledge* only | `DELTA` |
| **2. Gmail API (Workspace)** — Pub/Sub push, **metadata only** (sender, recipient, thread, ts) | email sign-off events | OrgMap + **SignOffEvent** node in L2 | `DELTA` |
| **3. ERP Integration** — CDC/outbox streaming | Crew DB · Contract (CLM) · Vessel/Port DB | L2 entities | `DELTA` |

> **Body content is never ingested for Gmail** (metadata only). **No tool secrets or source
> bodies enter any hosted container** — same boundary the managed-agents layer keeps
> ([`ARCHITECTURE.md`](../../ARCHITECTURE.md) §0).

### 1.2 Exit criteria (the scoreboard)

- [ ] All 5 sources **streaming** (not batch)
- [ ] Sign-off email → L2 **SignOffEvent** node in **< 5 min**
- [ ] **50 crew records**, **0 data loss**
- [ ] Changes propagate downstream in **< 5 min**

---

## 2. Target Architecture (what we build in `L1SignalFabric/`)

A standalone **Python 3.12 / FastAPI** service (matches the project stack in
[`README.md`](../../README.md)) that mirrors the upstream interface ladder, but as a
*push/stream* engine.

![Figure 1 — L1 SignalFabric connector architecture: real-time push ingress → connector → normalizer → event bus → L2 sink (OrgMap + SignOffEvent).](images/architecture.png)

### 2.1 Core abstractions (mirror the upstream pipeline)

- **`EventStreamConnector`** — Phase-3 analogue of the upstream `CDCExtractor`:
  `subscribe()`, `verify(req)`, `position()` (resume watermark), `emit() -> SignalEvent`.
- **`SignalEvent`** — canonical record, 1:1 with the upstream `Record`
  (`entity`, `key`, `sourceSystem`, `tenantId`, `data`, `operation=DELTA`, `timestamp`,
  `extractedAt`, `lineage`). Reusing the shape means L2/downstream stays batch-compatible.
- **`SourceSystem`** — extends the upstream enum (`SLACK`, `EMAIL` already exist;
  add `CREW_DB`, `CONTRACT_CLM`, `VESSEL_PORT_DB` under an `ERP` family).
- **`EventBus`** — `InMemoryBus` (Day 1 demo) → `RedisStreamsBus` (Day 4, durable + replay).
- **`L2Sink`** — idempotent upsert into OrgMap; special-cases sign-off emails into a
  **SignOffEvent** node. At-least-once delivery + DLQ.
- **`CheckpointStore`** — per-connector position for crash-safe resume (0 data loss).

### 2.2 Why a new service, not an edit to the upstream pipeline

L1 SignalFabric is built as a standalone module that **vendors the contract** (the
`Record`/`SourceSystem` shapes) into `L1SignalFabric/core/` and keeps wire compatibility, so
the work can later be folded back upstream as its real Phase-3 `CDCExtractor`
implementations — without modifying the existing upstream codebase.

---

## 3. Parallel Work Split

Two independent tracks with a **stable seam = the `EventBus` + `SignalEvent` contract**.
Track A owns everything *left* of the bus (ingress/connectors); Track B owns the bus
internals and everything *right* of it (normalize→sink→L2→observability). Either side can be
demoed against a mock on the other side, so **neither blocks the other**.

| | **Track A — Sreekumar** (Ingress & Connectors) | **Track B — Sruthy** (Core, Sink & Observability) |
|---|---|---|
| Owns | Slack Events receiver, ERP connector, connector framework, signature/verification, dedup | Event bus, normalizer, L2 sink + SignOffEvent, checkpoint/retry/DLQ, dashboard, test harness |
| Day 1 | Scaffold + `EventStreamConnector` iface + Slack & ERP skeletons + Slack URL-verify handshake | `SignalEvent` schema + `InMemoryBus` + `L2Sink` stub + SSE `/stream` + mock-event generator + `docker-compose` |
| Day 2 | Real Slack Events (signature verify, message/reaction/join, dedup by `event_id`) | Slack→OrgMap normalizer + OrgMap upsert + live OrgMap viewer + retry/DLQ |
| Day 3 | Sign-off email → **SignOffEvent** creation + latency instrumentation (<5 min) | Gmail Pub/Sub push endpoint + `watch()` + `history.list` **metadata-only** + thread/edge mapping |
| Day 4 | ERP CDC/outbox streaming (Crew/Vessel-Port/Contract) + 50-record load + 0-loss proof | `RedisStreamsBus` + `CheckpointStore` resume + per-source latency metrics + failure/retry suite |
| Day 5 | All-source live wiring + Slack/ERP test cases + Design-doc sections owned | Consolidated dashboard + full test suite run + Test-doc + Design-doc data-flow diagram |

**Shared/agreed Day 1 (pair for 1 hr):** lock `SignalEvent` JSON schema, bus interface,
and the `/stream` event envelope. Everything after keys off those.

---

## 4. Day-by-Day Plan — each day a clean end-to-end demo

> **Demo rule:** every day ends with `make demo` that runs green from a cold start and shows a
> visible event traveling source → L2. No day depends on un-demoed scaffolding.

### Day 1 — Jun 08 (Mon): Skeleton end-to-end "first signal"
**Goal:** one synthetic event flows ingress → bus → normalizer → L2 store → live tail.

- A (Sreekumar): repo scaffold under `L1SignalFabric/`; `EventStreamConnector` interface;
  Slack + ERP connector **skeletons**; Slack `url_verification` challenge endpoint;
  `/healthz`.
- B (Sruthy): `SignalEvent` model (mirrors `Record`); `InMemoryBus`; `L2Sink` stub
  (append-only JSONL + WebSocket/SSE broadcast); `GET /stream` (SSE live tail);
  `scripts/inject_mock.py` (emits a fake Slack msg + fake email-metadata event);
  `docker-compose.yml` + `Makefile`.

**✅ Demo 1:** `make up && make demo` → injected mock Slack message **and** mock email
metadata appear, normalized, in the L2 JSONL store and scroll live on the SSE dashboard.
Health check green. *(Proves the whole pipe with mocks.)*

---

### Day 2 — Jun 09 (Tue): Slack Events API live
**Goal:** a **real** Slack action shows up in OrgMap within seconds.

- A: Slack Events API receiver — `X-Slack-Signature` HMAC verification, handle
  `message`, `reaction_added`, `member_joined_channel`; idempotent dedup on `event_id`;
  **Socket Mode** fallback so the demo works without a public URL (or ngrok tunnel).
- B: Slack→canonical normalizer (reuse the field shape from the existing Slack scraper model);
  **OrgMap upsert** (person ↔ channel ↔ interaction edges = tribal knowledge); live OrgMap
  viewer pane; retry + DLQ on sink failure.

**✅ Demo 2:** post a message, add a reaction, and join a channel in a test Slack workspace →
all three surface in the OrgMap viewer in real time, deduped, with retry shown on a forced
sink error. *(Slack source = streaming ✔)*

---

### Day 3 — Jun 10 (Wed): Gmail metadata + SignOffEvent — **PROTOTYPE MILESTONE**
**Goal:** email metadata streams to OrgMap; a sign-off email auto-creates a SignOffEvent < 5 min.

- B: Gmail push pipeline — `users.watch()` registration → Google Cloud **Pub/Sub** topic →
  `POST /gmail/push` (JWT-verified) → `history.list` pulls **metadata only**
  (from/to/cc, thread, timestamp — **never body**); map participants → OrgMap edges.
- A: **Sign-off detector** → create **SignOffEvent** node in L2; end-to-end **latency
  instrumentation** (`extractedAt` → `sinkAt`) with a <5-min SLO gauge; idempotency so a
  re-delivered Pub/Sub message doesn't double-create.

**✅ Demo 3 (Proto):** send a normal email in the test Workspace → metadata edge appears in
OrgMap; send a **sign-off** email → a **SignOffEvent** node is created in L2 and the latency
gauge reads **< 5 min**. Slack still streaming alongside. *(Hits Jun-10 Proto + the sign-off
exit criterion.)*

---

### Day 4 — Jun 11 (Thu): ERP Integration + durability
**Goal:** ERP changes stream as DELTAs; restart loses nothing; 50-record load is clean.

- A: ERP connector covering **Crew DB · Vessel/Port DB · Contract (CLM)** via CDC/outbox
  pattern (watermark on `updated_at` or an `outbox` table) emitting `DELTA` records — the
  real Phase-3 `CDCExtractor` shape. Seed + drive **50 crew records**; prove **0 data loss**.
- B: swap `InMemoryBus` → **`RedisStreamsBus`** (durable, replayable); **`CheckpointStore`**
  so a connector resumes from last position after a kill; per-source **latency dashboard**;
  **failure/retry** test suite (drop, restart, duplicate, poison message → DLQ).

**✅ Demo 4:** update a crew row → DELTA reaches L2 **< 5 min**; `kill` a connector mid-stream
and restart → it resumes with **0 lost / 0 duplicated** records; run the **50-record** batch →
count reconciles exactly. *(All 5 sources now streaming ✔)*

---

### Day 5 — Jun 12 (Fri): Full E2E hardening + docs — **DESIGN & TEST DOCS DUE**
**Goal:** everything streaming at once; tests pass live; docs ready for async review.

- A + B together: run **Slack + Gmail + ERP** simultaneously into one consolidated dashboard;
  finalize the **Design doc** (connector architecture, stream schema, upstream-extension
  approach, data-flow diagram) and **Test doc** (50-record ingestion, per-source latency,
  sign-off trigger, failure/retry).

**✅ Demo 5 (final):** full live end-to-end — real Slack action, real email metadata + a
sign-off → SignOffEvent, and an ERP crew update, all landing in L2 < 5 min on one screen;
then run the **test suite live** (50-record, latency, sign-off, failure/retry) — all green.

---

## 5. Demo Safety Net (so every day is "perfectly showable")

| Risk | Mitigation kept ready each day |
|---|---|
| No public URL for Slack/Gmail webhooks | **Socket Mode** (Slack) + **ngrok**/Cloud Run tunnel; `scripts/replay.py` replays captured real payloads |
| Google Workspace / Pub/Sub not provisioned in time | Pub/Sub **emulator** + recorded push payloads; same `/gmail/push` code path |
| Live Slack workspace unavailable | recorded `event_id`-stamped fixtures under `fixtures/` |
| ERP DB not ready | local Postgres (already in stack) seeded with 50 crew rows + outbox table |
| Flaky network during demo | every connector has a **fixture/replay mode**; `make demo` defaults to a deterministic path, `make demo-live` for real sources |

Each demo is driven by a single `make demo-dayN` target committed the same day, so a cold
checkout reproduces it.

---

## 6. Deliverables checklist (Due Jun 12, async review)

**Design doc** — connector architecture · `SignalEvent` stream schema · upstream-extension
approach (Phase-3 `CDCExtractor`) · data-flow diagram.
**Test doc** — 50-record ingestion test · latency test per source · sign-off trigger test ·
failure/retry scenarios.

**Code (`L1SignalFabric/`):** core contracts, 3 connectors (Slack/Gmail/ERP), event bus
(in-mem + Redis), L2 sink + SignOffEvent, checkpoint/DLQ, dashboard, `make demo-day1..5`,
test suite.

---

## 7. Definition of Done (per exit criteria)

| Exit criterion | Proven by | Day |
|---|---|---|
| All 5 sources streaming (not batch) | Day-5 consolidated live demo | 5 |
| Sign-off email → L2 node < 5 min | Day-3 SignOffEvent + latency gauge | 3 |
| 50 crew records, 0 data loss | Day-4 load + reconciliation | 4 |
| Changes propagate downstream < 5 min | Day-4 per-source latency dashboard | 4 |
</content>
