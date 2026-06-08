# L1 SignalFabric — Real Connectors

Six production-shaped source connectors, modeled on the Conduit `scrapers/`
(Slack, Notion) but realized as L1 **streaming** connectors: each implements
`core.EventStreamConnector`, emits the canonical `SignalEvent`, and publishes to
the event bus instead of writing batch JSONL. Every feature the scrapers had —
real API clients, rate-limiting + retry, pagination, user caching, the Notion
block parser, AWS Secrets Manager token resolution, structured logging,
Conduit-compatible output, per-run metrics, CLIs — is preserved and shared.

The four vendor SDKs (`slack_sdk`, `notion_client`, `googleapiclient`, `msal`)
are **not** required: every client is built directly on `requests` (HTTP APIs) or
SQLAlchemy (DB), so the package imports and the demo runs without them. Each
connector also boots in **fixture/replay mode** when its credentials are blank,
which is the plan's "demo safety net".

## Layout

```
connectors/
  common/        shared infra (reused by every connector)
    http.py            RateLimitedClient — pacing, 429/5xx retry, metrics, pagination
    graph.py           GraphClient — Microsoft Graph (Outlook + SharePoint), OAuth2
    msgraph_webhook.py Graph subscription-validation + clientState verification
    email.py           shared e-mail metadata → SignalEvent (+ sign-off detection)
    writer.py          OutputWriter — <source>.jsonl + manifest.json + metrics.json
    metrics.py         ScrapeMetrics — per-run counters
    logger.py          StructuredLogger — JSON logs (ported from the scrapers)
    secrets.py         token resolution (literal / env / AWS Secrets Manager) + ts parse
    poller.py          PollingConnector — watermark position/commit base
  slack/         Events API (push)  + Web API backfill (pull): channels, history,
                 threads, reactions, user cache. CLI: test / list-channels / scrape
  notion/        pages + databases + blocks (pull, incremental by last_edited_time).
                 Full block parser (25+ types) + 20+ property types. CLI: test /
                 list-pages / scrape
  gmail/         Pub/Sub push + history pull, METADATA ONLY. CLI: test / watch / backfill
  outlook/       Microsoft Graph mail webhook + delta pull, METADATA ONLY.
                 CLI: test / backfill
  sharepoint/    Microsoft Graph drives + lists, delta pull + webhook.
                 CLI: test / list-drives / backfill
  database/      generic SQL CDC/outbox pull (the Phase-3 CDCExtractor): OutboxAdapter,
                 UpdatedAtAdapter, in-memory mimic. CLI: test / poll
  erp/           the original ERP outbox connector (kept; Database generalizes it)
```

## Ingestion shapes

| Connector  | Push (verify+ingest)        | Pull (poll, watermark)              | SourceSystem |
|------------|-----------------------------|-------------------------------------|--------------|
| Slack      | `/slack/events` HMAC        | Web-API backfill (newest `ts`)      | `SLACK`      |
| Gmail      | `/gmail/push` token/OIDC    | `history.list` (`historyId`)        | `GMAIL`      |
| Outlook    | `/outlook/webhook` Graph    | `messages/delta` (`@odata.deltaLink`)| `OUTLOOK`   |
| SharePoint | `/sharepoint/webhook` Graph | `drive/list delta` (per-target)     | `SHAREPOINT` |
| Notion     | —                           | `search` (`last_edited_time`)       | `NOTION`     |
| Database   | —                           | outbox `seq` / `updated_at`         | `DATABASE`   |

Gmail and Outlook are **metadata only** by design (From/To/Cc/Subject/thread/
labels/date) — bodies are never fetched. A `crew/sign-off` message carries
`l2Intent = CREATE_SIGNOFF_EVENT` so the L2 sink materializes a SignOffEvent.

## Running

```bash
# Live FastAPI app (connectors auto-detect creds from env; blank => fixture mode)
uvicorn api.app:app

# Backfill to a Conduit-compatible JSONL bundle
python -m connectors.slack.cli  scrape   --config connectors/slack/config.example.yaml
python -m connectors.notion.cli scrape   --token ntn_... --since 2024-01-01
python -m connectors.gmail.cli  backfill  --token <oauth> --query "newer_than:30d"
python -m connectors.database.cli poll    --url postgresql+psycopg2://u:p@h/db --mode outbox
```

Credentials resolve as: CLI flag → environment variable → YAML `--config` → AWS
Secrets Manager ARN. See `config.py` for every env var.
