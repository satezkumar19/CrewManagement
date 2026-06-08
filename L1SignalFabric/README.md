# L1 SignalFabric

Continuous event-stream ingestion for the **Maritime Crew Orchestrator**. L1
observes the source systems — **Slack, Gmail, Outlook, SharePoint, Notion, and
any SQL database** (plus the ERP outbox) — and normalizes their events into a
single canonical stream — `SignalEvent` — that the L2 knowledge graph (OrgMap +
**SignOffEvent**) consumes.

> **Design principle:** continuous streams, *not* batch snapshots. Every event is
> an append-only, typed, timestamped `SignalEvent` with `operation = DELTA`.

See [`docs/DESIGN.md`](docs/DESIGN.md), [`docs/PLAN.md`](docs/PLAN.md),
[`docs/TEST.md`](docs/TEST.md), and — for the component-by-component **Role &
Responsibility / Where it's implemented** map —
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md)
([`.docx`](docs/L1_SignalFabric_Implementation.docx)).

## Scope of this scaffold — Day 1 (Jun 08), Sreekumar's track

This is the **ingress / connector foundation**:

- `core/` — the agreed contracts:
  - `SignalEvent` canonical model + `SourceSystem` / `Operation` enums (`core/signal.py`)
  - `EventStreamConnector` interface — `verify / ingest / position / commit` (`core/connector.py`)
  - `EventBus` Protocol + `LoggingEventBus` **placeholder** (`core/bus.py`) — replaced
    by the InMemoryBus/RedisStreamsBus on the core track
  - `dedup_key()` and watermark stores (`core/dedup.py`, `core/watermark.py`)
- `connectors/slack/` — Slack Events API connector skeleton: **url_verification
  handshake**, HMAC signature verification (with a dev bypass), and message /
  reaction / channel-join mappers
- `connectors/erp/` — ERP connector skeleton: transactional-**outbox** fetch
  adapter (mimic) + watermark + per-table mappers (Crew DB / Contract-CLM /
  Vessel-Port DB)
- `api/` — FastAPI app exposing **`/healthz`** and **`POST /slack/events`**

The real **InMemoryBus** (dedup + subscriber fan-out + replay) now lives in
[`core/bus.py`](core/bus.py) and the L2 sink subscribes to it via
`create_app(bus=InMemoryBus())`. Still deferred (later days): the
**RedisStreamsBus** (Day 4, same Protocol — `docker-compose` ships the `redis`
seam) and the **Gmail** connector (Day 3).

See [`docs/COMPONENTS.md`](docs/COMPONENTS.md) for the six-component status map
and diagrams (SignalEvent · InMemoryBus · L2Sink · SSE `/stream` ·
mock-event generator · docker-compose).

## Quick start

```bash
# uses Python 3.11+
python -m pip install -r requirements.txt

make test        # contract, signal, API, signature, ERP watermark, InMemoryBus
make smoke       # in-process Day-1 ingress demo (no external services)
make run         # uvicorn on :8001
```

Or run the whole stack in containers (service + the Redis seam):

```bash
docker compose up --build       # → dashboard at http://localhost:8001/
```

Then:

```bash
curl localhost:8001/healthz

# Slack URL-verification handshake (what Slack sends when you set the Request URL)
curl -sX POST localhost:8001/slack/events \
  -H 'content-type: application/json' \
  -d '{"type":"url_verification","challenge":"abc123"}'      # -> abc123

# A Slack message event
curl -sX POST localhost:8001/slack/events -H 'content-type: application/json' -d '{
  "type":"event_callback","event_id":"Ev01","team_id":"T001",
  "event":{"type":"message","channel":"C005","user":"U002","text":"hi","ts":"1719980964.000100"}
}'                                                            # -> {"ok":true,"ingested":1}
```

## Connectors — configure & run

L1 ships **six real source connectors** plus the ERP outbox. Each one follows the
same `EventStreamConnector` contract and reuses the shared infrastructure in
[`connectors/common/`](connectors/common/) (rate-limit + retry HTTP, pagination,
Secrets-Manager token resolution, structured logging, Conduit-compatible output,
watermark checkpointing). See [`connectors/README.md`](connectors/README.md) for
the per-connector internals.

### Two ingestion shapes

| Connector  | Push (webhook → bus)             | Pull (poll, watermark-checkpointed)        | `SourceSystem` |
|------------|----------------------------------|--------------------------------------------|----------------|
| Slack      | `POST /slack/events` (HMAC)      | Web-API backfill — channels/history/threads| `SLACK`        |
| Gmail      | `POST /gmail/push` (token/OIDC)  | `history.list` since `historyId`           | `GMAIL`        |
| Outlook    | `POST /outlook/webhook` (Graph)  | `messages/delta`                           | `OUTLOOK`      |
| SharePoint | `POST /sharepoint/webhook` (Graph)| `drive`/`list` `delta` per target         | `SHAREPOINT`   |
| Notion     | —                                | `search` since `last_edited_time`          | `NOTION`       |
| Database   | —                                | outbox `seq` / `updated_at` high-watermark | `DATABASE`     |

> **Gmail & Outlook are metadata-only** (From/To/Cc/Subject/thread/labels/date —
> never the body). A `crew/sign-off` message carries `l2Intent =
> CREATE_SIGNOFF_EVENT`, so the L2 sink materializes a **SignOffEvent**.

### Fixture mode vs live mode (no config required to start)

Every connector boots in **fixture/replay mode** when its credentials are blank —
push endpoints accept replayed payloads and pull connectors no-op — so a fresh
checkout runs the demo with **no secrets**. Supplying a token/URL (below)
auto-upgrades that connector to **live** with no other change. The wiring is in
[`api/app.py`](api/app.py); every setting has a dev-safe default in
[`config.py`](config.py).

**Credential resolution order** (highest first): CLI flag → environment variable
→ YAML `--config` file → AWS Secrets Manager ARN (`pip install -e ".[aws]"`).

### Configuration (environment variables)

```bash
export L1_TENANT_ID=maritime-acme            # tenant stamped on every event

# Slack — Events API (push) needs the signing secret; Web-API backfill needs a bot token
export SLACK_SIGNING_SECRET=...              # verifies POST /slack/events (HMAC)
export SLACK_TOKEN=xoxb-...                  # bot token for the Web-API backfill CLI

# Gmail — Pub/Sub push (metadata only)
export GMAIL_ACCESS_TOKEN=ya29....           # OAuth access token (history pull)
export GMAIL_PUBSUB_TOKEN=...                # shared-secret echoed on ?token= (push auth)
# export GMAIL_OIDC_AUDIENCE=https://you/gmail/push   # alternative: verify the Pub/Sub OIDC JWT

# Outlook + SharePoint — Microsoft Graph (one app, two sources)
export OUTLOOK_ACCESS_TOKEN=...              # or use the app-credential trio below
export OUTLOOK_CLIENT_STATE=...              # secret echoed in webhook notifications
export SHAREPOINT_CLIENT_STATE=...
export MS_TENANT_ID=...  MS_CLIENT_ID=...  MS_CLIENT_SECRET=...   # client-credentials grant

# Notion
export NOTION_TOKEN=ntn_...                  # internal integration token

# Database — generic SQL CDC/outbox; blank => in-memory mimic (demo)
export DATABASE_URL=postgresql+psycopg2://user:pass@host/db   # pip install -e ".[postgres]"
export DATABASE_OUTBOX_TABLE=signal_outbox
export DATABASE_WATERMARK_PATH=./data/db.wm.json             # persist the cursor across restarts
```

With those exported, the connectors go live automatically:

```bash
make run            # uvicorn on :8001 — connectors auto-detect creds
curl localhost:8001/healthz   # lists every wired connector + its source_system
```

### Push connectors — register the webhook

Point each provider's webhook at the matching endpoint. The handshake each
provider sends is handled automatically (Slack `url_verification`, Graph
`validationToken`, Gmail Pub/Sub `?token=`):

```bash
# Microsoft Graph subscription-validation handshake (Outlook / SharePoint) — echoes the token
curl -sX POST 'localhost:8001/outlook/webhook?validationToken=PING'      # -> PING

# Gmail Pub/Sub push (envelope shown abbreviated; metadata is expanded via history.list)
curl -sX POST 'localhost:8001/gmail/push?token=$GMAIL_PUBSUB_TOKEN' \
  -H 'content-type: application/json' \
  -d '{"message":{"data":"<base64 historyId notification>","messageId":"p1"}}'
```

| Provider     | Endpoint                       | Register with                                   |
|--------------|--------------------------------|-------------------------------------------------|
| Slack        | `/slack/events`                | Slack app → Event Subscriptions → Request URL   |
| Gmail        | `/gmail/push`                  | `users.watch` → Pub/Sub push subscription       |
| Outlook      | `/outlook/webhook`             | Graph `POST /subscriptions` (resource = mail)   |
| SharePoint   | `/sharepoint/webhook`          | Graph `POST /subscriptions` (resource = drive/list) |

### Pull connectors — backfill / poll from the CLI

Every connector has a CLI (`test` to verify creds; a `scrape`/`backfill`/`poll`
that writes a **Conduit-compatible bundle** — `<source>.jsonl` + `manifest.json`
+ `metrics.json` — to `--output-dir`). Credentials use the same resolution order.

```bash
# Slack — backfill channel history (+threads), resolving users & reactions
python -m connectors.slack.cli test    --token xoxb-...
python -m connectors.slack.cli scrape  --config connectors/slack/config.example.yaml
python -m connectors.slack.cli scrape  --token xoxb-... --channels all --since 2024-01-01

# Notion — pages + database items (incremental by last_edited_time)
python -m connectors.notion.cli test       --token ntn_...
python -m connectors.notion.cli list-pages --token ntn_... --type page
python -m connectors.notion.cli scrape     --config connectors/notion/config.example.yaml

# Gmail — metadata-only backfill over a search window
python -m connectors.gmail.cli watch    --token $GMAIL_ACCESS_TOKEN --topic projects/p/topics/t
python -m connectors.gmail.cli backfill --token $GMAIL_ACCESS_TOKEN --query "newer_than:30d"

# Outlook — metadata-only Graph mail backfill (token or app creds)
python -m connectors.outlook.cli backfill --token $OUTLOOK_ACCESS_TOKEN --folder inbox

# SharePoint — drive items via Graph delta
python -m connectors.sharepoint.cli test       --token <graph> --hostname contoso.sharepoint.com --site-path /sites/Crew
python -m connectors.sharepoint.cli backfill   --token <graph> --drive-id <id>

# Database — generic SQL CDC; resumes from the persisted watermark
python -m connectors.database.cli test --url $DATABASE_URL
python -m connectors.database.cli poll --url $DATABASE_URL --mode outbox --table signal_outbox \
  --watermark-path ./data/db.wm.json
python -m connectors.database.cli poll --url $DATABASE_URL --mode updated-at --table crew --entity crew
```

YAML config files (`--config`) are provided for the two ported scrapers:
[`connectors/slack/config.example.yaml`](connectors/slack/config.example.yaml) and
[`connectors/notion/config.example.yaml`](connectors/notion/config.example.yaml).

## Demo data & streaming

A large, deterministic, **streamable** maritime crew-ops dataset (~4,600 events
across Slack / Email / ERP) drives the demo — see [`demo/README.md`](demo/README.md).

```bash
make seed           # generate ./data (backlog + live runway + entities + meta)
make stream         # drain the historical backlog through the connectors (idempotent)
make stream-live    # replay the future runway on a virtual clock (world in motion)
```

### Demo 1 — the whole pipe in the browser

```bash
make up && make demo      # start server (bg) + inject one Slack msg + one sign-off email
# then open the dashboard:
open http://localhost:8001/
make down                 # stop the server
```

**Goal:** one synthetic event flows **ingress → normalizer → bus → L2 store → live
tail**. `make demo` injects a mock Slack message and a mock sign-off email (built
from the generated mock data); they are normalized to `SignalEvent`s, published on
the bus, written to the **L2 JSONL store** (`data/l2_store.jsonl`) — the sign-off
email becoming a **SignOffEvent** node — and scroll live on the dashboard. Health
is green throughout.

`GET /` is a single-file dashboard ([`api/static/dashboard.html`](api/static/dashboard.html)):
a **pipeline view** with a live count at each stage, a **Run Demo 1** button that
shows each event's `raw → normalized → L2 record` trace, plus **Start live** /
**Load history** to replay the full dataset. It subscribes to **`GET /stream`**
(SSE); `POST /demo/{inject,start,stop,backlog}` drive it. The L2 sink
([`l2/store.py`](l2/store.py)) and the SSE `BroadcastBus`
([`api/live.py`](api/live.py)) both implement swap-in seams for Sruthy's real
InMemoryBus + L2 graph sink.

## Layout

```
L1SignalFabric/
  core/                 # SignalEvent, EventStreamConnector, EventBus, dedup, watermark
  connectors/
    common/             # shared: rate-limit HTTP, Graph client, webhook verify,
                        #   email mapper, writer, metrics, logger, secrets, poller
    slack/              # Events API (push) + Web-API backfill (pull) + client/cache/cli
    notion/             # pages/databases/blocks pull + block_parser + client + cli
    gmail/              # Pub/Sub push + history pull (metadata only) + verify + cli
    outlook/            # Graph mail webhook + delta pull (metadata only) + cli
    sharepoint/         # Graph drives/lists delta + webhook + cli
    database/           # generic SQL CDC/outbox adapters + connector + cli
    erp/                # original ERP outbox connector (Database generalizes it)
  api/
    app.py              # FastAPI factory (wires every connector + bus + L2 sink)
    routes/             # health, slack, gmail (/gmail/push),
                        #   graph_webhooks (/outlook/webhook, /sharepoint/webhook)
    live.py             # BroadcastBus + /stream (SSE) + / (dashboard) + /demo/*
    static/dashboard.html  # single-file pipeline dashboard
  l2/
    store.py            # L2 JSONL store + sink (SignalEvent -> OrgMap/SignOffEvent)
  demo/                 # generator + seed + stream (Freight-style demo data)
  data/                 # generated dataset (reproducible via `make seed`)
  scripts/smoke.py      # Day-1 ingress smoke
  tests/                # pytest suite
  docs/                 # PLAN / DESIGN / TEST / COMPONENTS / IMPLEMENTATION (+ .docx, diagrams)
  Dockerfile            # service image
  docker-compose.yml    # signalfabric + redis (Day-4 RedisStreamsBus seam)
```

## The seam (how the two tracks meet)

Connectors emit `SignalEvent` and publish via the `EventBus` Protocol. The core
track implements that Protocol (`InMemoryBus`) and subscribes the L2 sink — no
change to any connector or route. The Slack route already publishes; swap the bus
in `create_app(bus=...)` to integrate.
