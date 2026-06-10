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
Secrets-Manager token resolution, structured logging, batch-compatible output,
watermark checkpointing). See [`connectors/README.md`](connectors/README.md) for
the per-connector internals.

### Two ingestion shapes

| Connector  | Push (webhook → bus)             | Pull (poll, watermark-checkpointed)        | `SourceSystem` |
|------------|----------------------------------|--------------------------------------------|----------------|
| Slack      | `POST /slack/events` (HMAC)      | Web-API backfill — channels/history/threads| `SLACK`        |
| Gmail      | `POST /gmail/push` (token/OIDC)  | `history.list` since `historyId`           | `GMAIL`        |
| Outlook    | `POST /outlook/webhook` (Graph)  | unread poll + mark-read (app-only)         | `OUTLOOK`      |
| SharePoint | `POST /sharepoint/webhook` (Graph)| folder listing by path (app-only)         | `SHAREPOINT`   |
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

> **Setting up real accounts?** [`docs/CONNECTOR_SETUP.md`](docs/CONNECTOR_SETUP.md)
> ([`.docx`](docs/L1_SignalFabric_Connector_Setup.docx)) is a step-by-step guide to
> creating and configuring an app/account for every connector (Slack app, Google
> Cloud + Pub/Sub, Azure AD app for Outlook/SharePoint, Notion integration, SQL
> outbox) for a **live** demo.

### Configuration (environment variables)

```bash
export L1_TENANT_ID=maritime-acme            # tenant stamped on every event

# Slack — Events API (push) needs the signing secret; Web-API backfill needs a bot token
export SLACK_SIGNING_SECRET=...              # verifies POST /slack/events (HMAC)
export SLACK_TOKEN=xoxb-...                  # bot token for the Web-API backfill CLI

# Gmail — Pub/Sub push (metadata only)
# Self-refreshing per-user OAuth: mint the refresh token once with
#   python -m connectors.gmail.cli authorize     # opens consent, prints the trio
export GMAIL_CLIENT_ID=...apps.googleusercontent.com
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=1//...            # connector mints fresh access tokens from these
export GMAIL_PUBSUB_TOKEN=...                # shared-secret echoed on ?token= (push auth)
export GMAIL_PUBSUB_TOPIC=projects/<project-id>/topics/gmail-signals   # default for `watch`

# Outlook + SharePoint — Microsoft Graph (one app-only app, two sources)
export MS_TENANT_ID=...  MS_CLIENT_ID=...  MS_CLIENT_SECRET=...   # client-credentials grant
export OUTLOOK_MAILBOX_UPN=mailbox@contoso.com                    # app-only has no /me
# Optional — hands-off push (Graph change subscription); otherwise both just poll:
export MS_WEBHOOK_BASE_URL=https://<public-host>   # /outlook/webhook + /sharepoint/webhook appended
export OUTLOOK_CLIENT_STATE=...   SHAREPOINT_CLIENT_STATE=...     # secret echoed + verified on each notification

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
| Outlook      | `/outlook/webhook`             | `connectors.outlook.cli subscribe` (or `make outlook-subscribe`) |
| SharePoint   | `/sharepoint/webhook`          | `connectors.sharepoint.cli subscribe` (or `make sharepoint-subscribe`) |

> **Outlook & SharePoint are hands-off-optional.** Both poll by default (no public
> URL needed). To make changes arrive without polling, set `MS_WEBHOOK_BASE_URL` +
> the `*_CLIENT_STATE` secrets and run `make outlook-subscribe` / `make
> sharepoint-subscribe` — this `POST /subscriptions` to Graph so notifications push
> to the webhook (which then kicks a poll). The server must be **publicly reachable**
> when you subscribe (Graph validates the URL), and subscriptions expire at ~3 days,
> so `… renew <id>` on a schedule. See [`docs/CONNECTOR_SETUP.md` §3.3/§4.2](docs/CONNECTOR_SETUP.md).

> **Gmail push needs an active `watch`, not just credentials.** After configuring,
> run `make gmail-watch` (re-run before the ~7-day expiry) and confirm the chain with
> `make gmail-doctor`. The one-time `make gmail-authorize` also requires your mailbox
> to be a **Test user** on the OAuth consent screen (or the app **Published**) — while
> in *Testing* mode the refresh token expires after 7 days. See
> [`docs/CONNECTOR_SETUP.md` §2](docs/CONNECTOR_SETUP.md).

### Pull connectors — backfill / poll from the CLI

Every connector has a CLI (`test` to verify creds; a `scrape`/`backfill`/`poll`
that writes a **batch-compatible bundle** — `<source>.jsonl` + `manifest.json`
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

# Gmail — metadata-only; creds come from the refresh-token trio in .env (or --token)
python -m connectors.gmail.cli authorize   # one-time consent → prints GMAIL_REFRESH_TOKEN
python -m connectors.gmail.cli test        # confirms creds; prints mailbox + historyId
python -m connectors.gmail.cli watch       # (re)register push; --topic defaults to GMAIL_PUBSUB_TOPIC
python -m connectors.gmail.cli backfill --query "newer_than:30d"

# Outlook — metadata-only Graph mail (app-only; creds + mailbox from .env or flags)
python -m connectors.outlook.cli test          # confirms Graph mail access for OUTLOOK_MAILBOX_UPN
python -m connectors.outlook.cli backfill --output-dir ./output   # unread messages → signals
python -m connectors.outlook.cli subscribe     # (optional) hands-off push; needs MS_WEBHOOK_BASE_URL

# SharePoint — app-only folder listing under one site's default document library
python -m connectors.sharepoint.cli test       # resolves the site/drive, lists SHAREPOINT_FOLDER_PATH
python -m connectors.sharepoint.cli backfill   --output-dir ./output   # drive_items → signals
python -m connectors.sharepoint.cli subscribe  # (optional) hands-off push; needs MS_WEBHOOK_BASE_URL

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
                        #   Graph subscriptions, email mapper, writer, metrics, logger, secrets, poller
    slack/              # Events API (push) + Web-API backfill (pull) + client/cache/cli
    notion/             # pages/databases/blocks pull + block_parser + client + cli
    gmail/              # Pub/Sub push + history pull (metadata only) + verify + cli
    outlook/            # Graph mail: unread poll + mark-read + webhook + subscribe cli (metadata only)
    sharepoint/         # Graph: app-only folder listing + webhook + subscribe cli
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
