# L1 SignalFabric — Connector Account Setup (Live Demo Guide)

How to create and configure a real account / app for **every** L1 connector so the
demo streams **live** data: Slack, Gmail, Outlook, SharePoint, Notion, and a SQL
Database. Each section is self-contained — set up only the sources you want live;
any connector left unconfigured simply stays in **fixture mode** and the rest of
the demo still runs.

> **Two modes, auto-detected.** With blank credentials a connector boots in
> fixture/replay mode (no secrets needed). The moment its credentials are present
> in the environment, [`api/app.py`](../api/app.py) wires it **live** with no code
> change. Every variable below has a dev-safe default in [`config.py`](../config.py).

---

## 0. Prerequisites

| Need | Why | How |
|---|---|---|
| Python 3.11+ + deps | run the service & CLIs | `pip install -r requirements.txt` (add `.[postgres]`, `.[aws]`, `.[google]` as needed) |
| A **public HTTPS URL** | Slack/Gmail **push** webhooks must reach your host (Outlook & SharePoint **poll** by default; a public URL is only needed for the optional hands-off Graph subscription — 3.3/4.2) | `ngrok http 8001` (dev) or deploy to Cloud Run / a public host |
| Admin rights | creating Slack apps, Google Cloud projects, Azure app registrations | workspace/tenant admin |

**Expose the local service for webhooks (dev):**

```bash
make run                         # uvicorn on :8001
ngrok http 8001                  # → https://<random>.ngrok-free.app  (your PUBLIC_URL)
export PUBLIC_URL=https://<random>.ngrok-free.app
```

Pull-only connectors (Notion, Database, and the backfill CLIs) need **no** public
URL. Outlook & SharePoint poll by default (no URL needed) but can opt into
hands-off push via a Graph change subscription (3.3/4.2), which does need one.

---

## 1. Slack

Slack has two paths, both supported: **Events API** (live push) and **Web-API
backfill** (history pull). For the live demo you want the Events API; the bot
token additionally enables backfill + user/reaction enrichment.

### 1.1 Create the app
1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
2. Name it (e.g. `SignalFabric`) and pick your workspace.

### 1.2 Add Bot Token Scopes
**OAuth & Permissions → Scopes → Bot Token Scopes**, add:

| Scope | For |
|---|---|
| `channels:history`, `channels:read` | read & list public channels |
| `groups:history` | private channels (if you pass explicit ids) |
| `users:read`, `users:read.email` | resolve display name + e-mail |
| `reactions:read` | reaction counts |

### 1.3 Subscribe to events (live push)
1. **Event Subscriptions → Enable Events**.
2. **Request URL:** `${PUBLIC_URL}/slack/events` — Slack sends a
   `url_verification` challenge; the connector echoes it automatically, so it
   turns **green** immediately.
3. **Subscribe to bot events:** `message.channels`, `reaction_added`,
   `member_joined_channel`.

### 1.4 Install & collect credentials
1. **Install App** to the workspace → copy the **Bot User OAuth Token** (`xoxb-…`).
2. **Basic Information → App Credentials** → copy the **Signing Secret**.
3. Invite the bot to the demo channels: `/invite @SignalFabric`.

### 1.5 Configure
```bash
export SLACK_SIGNING_SECRET=<signing secret>   # verifies POST /slack/events (HMAC)
export SLACK_TOKEN=xoxb-<bot token>            # backfill CLI + enrichment
```

### 1.6 Verify
```bash
python -m connectors.slack.cli test --token $SLACK_TOKEN   # prints team + member channels
# then post a message / add a reaction in a channel the bot is in → watch the dashboard
```

---

## 2. Gmail (metadata only)

Live Gmail uses **Google Cloud Pub/Sub push**: `users.watch` tells Gmail to
publish a change notification (a `historyId`) to a Pub/Sub topic, which pushes to
`/gmail/push`; the connector then pulls **metadata** for the new messages. Bodies
are never fetched.

### 2.1 Google Cloud project + APIs
1. <https://console.cloud.google.com> → create/select a project.
2. **APIs & Services → Enable APIs**: enable **Gmail API** and **Cloud Pub/Sub API**.

### 2.2 Pub/Sub topic + push subscription
1. **Pub/Sub → Topics → Create topic** (e.g. `gmail-signals`).
2. Grant Gmail permission to publish: on the topic, **add principal**
   `gmail-api-push@system.gserviceaccount.com` with role **Pub/Sub Publisher**.
3. **Create subscription** → **Delivery type: Push** →
   **Endpoint URL:** `${PUBLIC_URL}/gmail/push?token=<SHARED_SECRET>`
   (the `token` query param is the shared secret the connector checks).

### 2.3 OAuth client + a durable refresh token (recommended)
A raw access token expires in ~1 hour, but a `watch` lasts 7 days — so push
silently stops once the token goes stale. Use a **refresh token** instead: the
connector mints fresh access tokens itself.

1. **APIs & Services → OAuth consent screen:** configure it, add the
   `.../auth/gmail.readonly` scope, and add your mailbox account under **Test
   users** (or **Publish** the app — see the caveat below).
2. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Application type: Desktop app.** Note the **Client ID** and **Client secret**.
3. Mint the refresh token (opens a browser, captures the redirect on localhost,
   prints the trio):
   ```bash
   python -m connectors.gmail.cli authorize \
     --client-id <client id> --client-secret <client secret>
   ```
   On the consent screen you'll see *"Google hasn't verified this app"* → **Advanced
   → Go to … (unsafe)** — expected for your own unverified app.

> **Caveat — the 7-day refresh-token trap.** While the OAuth consent screen is in
> **Testing** mode, the *refresh token itself* expires after 7 days. For a durable
> token, set Publishing status to **In production** (`gmail.readonly` is a
> restricted scope, so a public app needs Google verification — but an internal /
> single-tenant app can run unverified within the user cap).
>
> *Quick-demo fallback:* skip 2.3 and paste a short-lived `GMAIL_ACCESS_TOKEN` from
> the [OAuth Playground](https://developers.google.com/oauthplayground) instead of
> the trio — it works for ~1 hour, fine for a one-shot test but not for push.

### 2.4 Register the watch
Reads the credentials and `GMAIL_PUBSUB_TOPIC` from `.env` (configure 2.5 first):
```bash
python -m connectors.gmail.cli watch        # or: make gmail-watch
# → "watch registered: historyId=… expiration=…"   (re-run before the ~7-day expiry)
```
A `403 / permission` error here means the topic doesn't exist or
`gmail-api-push@system.gserviceaccount.com` isn't a **Pub/Sub Publisher** on it (2.2).

### 2.5 Configure
```bash
export GMAIL_CLIENT_ID=<client id>              # the connector mints fresh access
export GMAIL_CLIENT_SECRET=<client secret>      #   tokens from this trio and expands
export GMAIL_REFRESH_TOKEN=<refresh token>      #   history → metadata
export GMAIL_PUBSUB_TOKEN=<SHARED_SECRET>       # must match the ?token= in 2.2
export GMAIL_PUBSUB_TOPIC=projects/<project-id>/topics/gmail-signals
export GMAIL_PUSH_ENDPOINT=${PUBLIC_URL}/gmail/push?token=<SHARED_SECRET>   # used by `make gmail-doctor`
# Alternative to the shared secret — verify the Pub/Sub OIDC JWT instead:
# export GMAIL_OIDC_AUDIENCE=${PUBLIC_URL}/gmail/push   # needs pip install ".[google]"
```

### 2.6 Verify
```bash
python -m connectors.gmail.cli test     # refreshes a token, prints mailbox + historyId
make gmail-doctor                       # walks the whole push chain, flags the broken link
# send an email to the watched mailbox → a GMAIL/email signal appears on the dashboard.
# Subject "Sign-off notification" or label crew/sign-off → an L2 SignOffEvent node.
```

---

## 3. Outlook (Microsoft Graph mail, metadata only)

Outlook and SharePoint share **one Azure AD app registration** and the
**client-credentials (app-only)** grant. Set this up once; reuse for both.

The connector **polls the mailbox for unread messages** and **marks each as read**
once emitted. Marking-as-read is the dedupe/checkpoint —
a restart simply re-lists whatever is still unread, so there are no gaps or
duplicates and nothing to persist. Because the flow is app-only there is no
signed-in user, so a **target mailbox UPN is required** (`/me` is delegated-only).

### 3.1 Register the app (shared with SharePoint)
1. <https://portal.azure.com> → **Azure Active Directory → App registrations →
   New registration** (e.g. `SignalFabric`). Note the **Application (client) ID**
   and **Directory (tenant) ID**.
2. **Certificates & secrets → New client secret** → copy the **secret value**.

### 3.2 Grant Graph application permissions
**API permissions → Add a permission → Microsoft Graph → Application permissions:**

| Permission | For |
|---|---|
| `Mail.ReadWrite` | Outlook mail — read **and** mark-as-read (this section) |
| `Sites.Read.All` *or* `Sites.Selected` | SharePoint folder listing (section 4) |

Then **Grant admin consent**. (`Mail.Read` alone suffices only if you set
`OUTLOOK_MARK_AS_READ=0`; the default marks messages read and needs `Mail.ReadWrite`.)

> **Least privilege for SharePoint:** prefer `Sites.Selected` over `Sites.Read.All`,
> then grant the app read on just the one site (PnP:
> `Grant-PnPAzureADAppSitePermission -AppId <client-id> -Permissions Read -Site <url>`,
> or `POST /sites/{id}/permissions`). Without that per-site grant the site lookup
> 404s even though auth succeeds. See section 4.

### 3.3 (Optional) Create a change subscription — hands-off push
The connector polls, so a webhook is **not required** — but a Graph **change
subscription** makes mail arrive *hands-off*: Graph POSTs a notification to
`/outlook/webhook` on each new message, which kicks an immediate unread-poll
(no manual trigger). Graph validates the URL synchronously at create time
(`validationToken` echoed within 10s — the connector does this automatically), so
**start the server publicly first** (section 0).

Register it with the connector CLI (no hand-rolled REST):
```bash
# reads MS_* + OUTLOOK_CLIENT_STATE + MS_WEBHOOK_BASE_URL from .env
python -m connectors.outlook.cli subscribe          # or: make outlook-subscribe
python -m connectors.outlook.cli subscriptions      # list id + expiry
python -m connectors.outlook.cli renew <id>         # before it lapses (see below)
```
`MS_WEBHOOK_BASE_URL` is your public HTTPS base (e.g. the ngrok host); the CLI
appends `/outlook/webhook`. `OUTLOOK_CLIENT_STATE` is a secret echoed in every
notification and **verified constant-time** by the webhook — non-empty means
pushes are authenticated, not dev-open. Under the hood the CLI sends:

```http
POST https://graph.microsoft.com/v1.0/subscriptions
{
  "changeType": "created",
  "notificationUrl": "${MS_WEBHOOK_BASE_URL}/outlook/webhook",
  "resource": "users/<mailbox-upn>/messages",
  "expirationDateTime": "<now + ~70h, ISO 8601>",
  "clientState": "<OUTLOOK_CLIENT_STATE>"
}
```

> **Renewal:** Graph caps mail subscriptions at ~3 days. Run `renew <id>` on a
> schedule (like `make gmail-watch`) before the printed `expires` time, or the
> push goes silent and you fall back to manual polling.

### 3.4 Configure
```bash
export MS_TENANT_ID=<tenant id>                 # client-credentials (app-only) grant
export MS_CLIENT_ID=<client id>
export MS_CLIENT_SECRET=<client secret>
export OUTLOOK_MAILBOX_UPN=<mailbox@contoso.com>  # REQUIRED (app-only — no /me)
# Optional:
# export OUTLOOK_MARK_AS_READ=0                  # leave messages unread (default: marks read)
# export OUTLOOK_CLIENT_STATE=<secret>           # hands-off push (3.3); verified on each notification
# export MS_WEBHOOK_BASE_URL=https://<public-host>  # hands-off push (3.3); /outlook/webhook appended
```

### 3.5 Verify
```bash
python -m connectors.outlook.cli test --tenant $MS_TENANT_ID \
  --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET \
  --mailbox $OUTLOOK_MAILBOX_UPN
# prints how many unread messages were sampled. Send mail to that mailbox, then
# poll → an OUTLOOK/email signal appears (subject "Sign-off notification" → SignOffEvent).
```

---

## 4. SharePoint (Microsoft Graph — document-library folders)

Reuses the Azure app from **section 3** (with `Sites.Read.All` *or* `Sites.Selected`
consented; add `Files.Read.All` only if you later download file content). One site
is addressed by **hostname + server-relative path**; the connector resolves its
default document library and **lists the configured folder(s) by path**, emitting a
`drive_item` event per file/folder. Re-listing is idempotent — an in-process
seen-set suppresses items already emitted.

> **`Sites.Selected` (least privilege):** the app sees only sites explicitly
> granted to it. After consenting `Sites.Selected`, an admin grants read on the
> target site (PnP `Grant-PnPAzureADAppSitePermission -AppId <client-id>
> -Permissions Read -Site https://<host>/sites/<name>`, or `POST
> /sites/{site-id}/permissions`). Symptom of a missing grant: `test` returns
> **404 site lookup failed** while `/subscriptions` and mail still work.

### 4.1 Find your site + folder
```bash
python -m connectors.sharepoint.cli test \
  --tenant $MS_TENANT_ID --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET \
  --hostname <contoso>.sharepoint.com --site-path /sites/<SiteName> \
  --folder "Shared Documents/<your-folder>"
# prints the resolved site id and the folder's immediate children (files + subfolders)
```

### 4.2 (Optional) Create a change subscription — hands-off push
The connector polls, so a webhook is **not required** — but a Graph **change
subscription** makes site changes arrive *hands-off*: Graph POSTs to
`/sharepoint/webhook` on any change in the document library, which kicks a folder
poll. The subscription targets the **drive root** (`changeType: updated` is the
only type `driveItem` supports). Start the server publicly first (Graph validates
the URL at create time).

Register it with the connector CLI (resolves the drive id for you):
```bash
# reads MS_* + SHAREPOINT_* + SHAREPOINT_CLIENT_STATE + MS_WEBHOOK_BASE_URL from .env
python -m connectors.sharepoint.cli subscribe       # or: make sharepoint-subscribe
python -m connectors.sharepoint.cli subscriptions   # list id + expiry
python -m connectors.sharepoint.cli renew <id>      # before it lapses (~3 days)
```
Under the hood:
```http
POST https://graph.microsoft.com/v1.0/subscriptions
{
  "changeType": "updated",
  "notificationUrl": "${MS_WEBHOOK_BASE_URL}/sharepoint/webhook",
  "resource": "drives/<drive-id>/root",
  "expirationDateTime": "<now + ~70h, ISO 8601>",
  "clientState": "<SHAREPOINT_CLIENT_STATE>"
}
```

### 4.3 Configure
```bash
# app creds from section 3 are reused; add the site + folder(s) to watch:
export SHAREPOINT_HOSTNAME=<contoso>.sharepoint.com
export SHAREPOINT_SITE_PATH=/sites/<SiteName>
export SHAREPOINT_FOLDER_PATH="Shared Documents/<folder>"   # comma/newline-separated for several
# Optional:
# export SHAREPOINT_CLIENT_STATE=<secret>                   # hands-off push (4.2); verified on each notification
# export MS_WEBHOOK_BASE_URL=https://<public-host>          # hands-off push (4.2); /sharepoint/webhook appended
```

### 4.4 Verify
```bash
python -m connectors.sharepoint.cli backfill \
  --tenant $MS_TENANT_ID --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET \
  --hostname $SHAREPOINT_HOSTNAME --site-path $SHAREPOINT_SITE_PATH \
  --folder "$SHAREPOINT_FOLDER_PATH" --output-dir ./output
# writes sharepoint.jsonl + manifest + metrics for the folder's items (metadata only)
```

---

## 5. Notion

Notion uses a single **internal integration token**; there is no webhook, so no
public URL is needed. The connector pulls pages/databases incrementally by
`last_edited_time`.

### 5.1 Create the integration
1. <https://www.notion.so/my-integrations> → **New integration** (Internal).
2. Capabilities: **Read content** (and **Read user information** for author e-mail).
3. Copy the **Internal Integration Token** (`ntn_…` / `secret_…`).

### 5.2 Share content with the integration
For every page/database to ingest: open it → **•••  → Connections → Add
connection → <your integration>**. (An integration only sees explicitly shared
content.)

### 5.3 Configure
```bash
export NOTION_TOKEN=<ntn_… integration token>
```

### 5.4 Verify
```bash
python -m connectors.notion.cli test       --token $NOTION_TOKEN   # confirms access
python -m connectors.notion.cli list-pages --token $NOTION_TOKEN   # shared pages/databases
python -m connectors.notion.cli scrape     --token $NOTION_TOKEN --output-dir ./output
```

---

## 6. Database (generic SQL CDC / outbox)

Stream changes from any SQL database SQLAlchemy can reach (Postgres, MySQL,
SQLite, …). Two strategies — choose one:

### 6.1 Option A — transactional outbox (recommended; captures inserts/updates/deletes)
Create an append-only outbox table and a trigger that writes a row on every
change. Example (Postgres):

```sql
CREATE TABLE signal_outbox (
  seq         BIGSERIAL PRIMARY KEY,
  table_name  TEXT        NOT NULL,
  op          TEXT        NOT NULL,          -- INSERT | UPDATE | DELETE
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload     JSONB       NOT NULL           -- the changed row (include its pk)
);

CREATE OR REPLACE FUNCTION crew_to_outbox() RETURNS trigger AS $$
BEGIN
  INSERT INTO signal_outbox(table_name, op, payload)
  VALUES ('crew', TG_OP, to_jsonb(COALESCE(NEW, OLD)));
  RETURN COALESCE(NEW, OLD);
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER crew_outbox
  AFTER INSERT OR UPDATE OR DELETE ON crew
  FOR EACH ROW EXECUTE FUNCTION crew_to_outbox();
```

### 6.2 Option B — `updated_at` high-watermark (no triggers; misses deletes)
Ensure the table has an `updated_at TIMESTAMPTZ` column that bumps on every write.

### 6.3 Configure
```bash
pip install -e ".[postgres]"                                  # psycopg2 driver
export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
export DATABASE_OUTBOX_TABLE=signal_outbox                    # Option A
export DATABASE_WATERMARK_PATH=./data/db.wm.json              # persist cursor across restarts
```

### 6.4 Verify
```bash
python -m connectors.database.cli test --url $DATABASE_URL --table signal_outbox
# Option A:
python -m connectors.database.cli poll --url $DATABASE_URL --mode outbox \
  --table signal_outbox --watermark-path ./data/db.wm.json
# Option B:
python -m connectors.database.cli poll --url $DATABASE_URL --mode updated-at \
  --table crew --entity crew
```

---

## 7. Putting it together — live demo runbook

```bash
# 1) tenant + the connectors you configured above
export L1_TENANT_ID=maritime-acme
#    (export the SLACK_/GMAIL_/MS_/OUTLOOK_/SHAREPOINT_/NOTION_/DATABASE_ vars)

# 2) start the service and confirm what's wired
make run
curl localhost:8001/healthz        # lists every live connector + its source_system

# 3) register the push webhooks at ${PUBLIC_URL}/{slack/events,gmail/push}
#    (sections 1.3, 2.2). Outlook & SharePoint: poll by default, OR go hands-off
#    with `make outlook-subscribe` / `make sharepoint-subscribe` (3.3, 4.2)

# 4) open the dashboard, then act in each app
open http://localhost:8001/
#    - Slack: post a message / add a reaction / join a channel
#    - Gmail/Outlook: send a mail (subject "Sign-off notification" → SignOffEvent)
#    - Notion: edit a shared page;   Database: update a row
#    - SharePoint: add/edit a file in the watched folder (SHAREPOINT_FOLDER_PATH)
#    Each change flows ingress → normalizer → bus → L2 store and scrolls live.
```

### Credential reference (all environment variables)

| Connector | Variables |
|---|---|
| Slack | `SLACK_SIGNING_SECRET`, `SLACK_TOKEN` |
| Gmail | `GMAIL_CLIENT_ID`+`GMAIL_CLIENT_SECRET`+`GMAIL_REFRESH_TOKEN` *(or a short-lived `GMAIL_ACCESS_TOKEN`)*; `GMAIL_PUBSUB_TOKEN` *(or `GMAIL_OIDC_AUDIENCE`)*; `GMAIL_PUBSUB_TOPIC`, `GMAIL_PUSH_ENDPOINT` |
| Outlook | `MS_TENANT_ID`+`MS_CLIENT_ID`+`MS_CLIENT_SECRET`+`OUTLOOK_MAILBOX_UPN`; optional `OUTLOOK_MARK_AS_READ`, `OUTLOOK_CLIENT_STATE`+`MS_WEBHOOK_BASE_URL` *(hands-off push)* |
| SharePoint | `MS_TENANT_ID`+`MS_CLIENT_ID`+`MS_CLIENT_SECRET`+`SHAREPOINT_HOSTNAME`+`SHAREPOINT_SITE_PATH`+`SHAREPOINT_FOLDER_PATH`; optional `SHAREPOINT_CLIENT_STATE`+`MS_WEBHOOK_BASE_URL` *(hands-off push)* |
| Notion | `NOTION_TOKEN` |
| Database | `DATABASE_URL`, `DATABASE_OUTBOX_TABLE`, `DATABASE_WATERMARK_PATH` |
| All | `L1_TENANT_ID` |

> **Secrets hygiene.** Any token above can instead be stored in AWS Secrets Manager
> and referenced by ARN (`--token-secret-arn` / `*_TOKEN_SECRET_ARN`); install with
> `pip install -e ".[aws]"`. Never commit real tokens — use the example YAML files
> ([`connectors/slack/config.example.yaml`](../connectors/slack/config.example.yaml),
> [`connectors/notion/config.example.yaml`](../connectors/notion/config.example.yaml))
> as templates only.

### Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Slack Request URL won't verify | service not public, or wrong path — must be `${PUBLIC_URL}/slack/events` |
| Graph subscription create fails validation | webhook must return the `validationToken` within 10s — confirm `${PUBLIC_URL}` is reachable |
| Gmail push returns 401 | `GMAIL_PUBSUB_TOKEN` doesn't match the `?token=` on the push subscription |
| Gmail `authorize` → `403 access_denied` | consent screen in Testing and your account isn't a **Test user** — add it (2.3.1), or publish the app |
| Gmail push arrives but `ingested: 0` | no server credential to expand history — set the refresh-token trio (2.5); `make gmail-doctor` flags this |
| Gmail email doesn't trigger anything | no active `watch` (run 2.4 / `make gmail-watch`), or the push subscription points at a stale ngrok URL — `make gmail-doctor` |
| Outlook connector missing / never polls | `OUTLOOK_MAILBOX_UPN` unset — app-only auth has no `/me`, so the target mailbox is required (3.4) |
| Outlook `mark_read` fails with 403 | app has `Mail.Read` but not `Mail.ReadWrite`; grant it (3.2) or set `OUTLOOK_MARK_AS_READ=0` |
| SharePoint `folder not found` / `site lookup failed` | wrong `SHAREPOINT_HOSTNAME`/`SITE_PATH`/`FOLDER_PATH`; or, with `Sites.Selected`, the app was never granted this site (3.2); or `Sites.Read.All` not admin-consented |
| Outlook/SharePoint 401 on notifications | `*_CLIENT_STATE` env var ≠ the `clientState` set when the subscription was created — re-`subscribe` after changing it |
| `subscribe` fails URL validation | server not publicly reachable at `MS_WEBHOOK_BASE_URL` when you ran it — Graph echoes the `validationToken` synchronously; start the tunnel + server first |
| Push went silent after a few days | the Graph subscription expired (~3-day cap) — `… subscriptions` to check, then `… renew <id>`; schedule it like `make gmail-watch` |
| Notion `list-pages` empty | the integration hasn't been added to any page (section 5.2) |
| Connector missing from `/healthz` | its credentials aren't set → still in fixture mode (expected) |
