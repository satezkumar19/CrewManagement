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
| A **public HTTPS URL** | Slack/Gmail/Outlook/SharePoint **push** webhooks must reach your host | `ngrok http 8001` (dev) or deploy to Cloud Run / a public host |
| Admin rights | creating Slack apps, Google Cloud projects, Azure app registrations | workspace/tenant admin |

**Expose the local service for webhooks (dev):**

```bash
make run                         # uvicorn on :8001
ngrok http 8001                  # → https://<random>.ngrok-free.app  (your PUBLIC_URL)
export PUBLIC_URL=https://<random>.ngrok-free.app
```

Pull-only connectors (Notion, Database, and the backfill CLIs) need **no** public
URL — only the push connectors do.

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

### 2.3 Get an access token
For a quick demo, mint an OAuth token with the **Gmail readonly/metadata** scope
(`https://www.googleapis.com/auth/gmail.readonly`) via the
[OAuth Playground](https://developers.google.com/oauthplayground) or a service
account with domain-wide delegation. Copy the **access token**.

### 2.4 Register the watch
```bash
python -m connectors.gmail.cli watch \
  --token <access token> \
  --topic projects/<project-id>/topics/gmail-signals
```
(Re-run before the ~7-day watch expiry.)

### 2.5 Configure
```bash
export GMAIL_ACCESS_TOKEN=<access token>        # used to expand history → metadata
export GMAIL_PUBSUB_TOKEN=<SHARED_SECRET>       # must match the ?token= in 2.2
# Alternative to the shared secret — verify the Pub/Sub OIDC JWT instead:
# export GMAIL_OIDC_AUDIENCE=${PUBLIC_URL}/gmail/push   # needs pip install ".[google]"
```

### 2.6 Verify
```bash
python -m connectors.gmail.cli test --token $GMAIL_ACCESS_TOKEN   # prints mailbox + historyId
# send an email to the watched mailbox → a GMAIL/email signal appears on the dashboard.
# Subject "Sign-off notification" or label crew/sign-off → an L2 SignOffEvent node.
```

---

## 3. Outlook (Microsoft Graph mail, metadata only)

Outlook and SharePoint share **one Azure AD app registration** and the
client-credentials grant. Set this up once; reuse for both.

### 3.1 Register the app (shared with SharePoint)
1. <https://portal.azure.com> → **Azure Active Directory → App registrations →
   New registration** (e.g. `SignalFabric`). Note the **Application (client) ID**
   and **Directory (tenant) ID**.
2. **Certificates & secrets → New client secret** → copy the **secret value**.

### 3.2 Grant Graph application permissions
**API permissions → Add a permission → Microsoft Graph → Application permissions:**

| Permission | For |
|---|---|
| `Mail.Read` | Outlook mail (this section) |
| `Sites.Read.All`, `Files.Read.All` | SharePoint (section 4) |

Then **Grant admin consent**.

### 3.3 Create the change subscription (live push)
Graph webhooks require the endpoint to answer the **validation handshake**
(`validationToken`) within 10s — the connector does this automatically. Create the
subscription (via Graph Explorer or `curl` with an app token):

```http
POST https://graph.microsoft.com/v1.0/subscriptions
{
  "changeType": "created",
  "notificationUrl": "${PUBLIC_URL}/outlook/webhook",
  "resource": "users/<mailbox-id>/mailFolders('inbox')/messages",
  "expirationDateTime": "<now + 3 days, ISO 8601>",
  "clientState": "<OUTLOOK_CLIENT_STATE>"
}
```

### 3.4 Configure
```bash
export MS_TENANT_ID=<tenant id>
export MS_CLIENT_ID=<client id>
export MS_CLIENT_SECRET=<client secret>         # client-credentials grant (auto-acquires Graph token)
export OUTLOOK_CLIENT_STATE=<secret in 3.3>     # authenticates inbound notifications
# Or skip app creds and pass a ready Graph token directly:
# export OUTLOOK_ACCESS_TOKEN=<graph access token>
```

### 3.5 Verify
```bash
python -m connectors.outlook.cli test --tenant $MS_TENANT_ID \
  --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET
# send mail to the subscribed mailbox → an OUTLOOK/email signal appears.
```

---

## 4. SharePoint (Microsoft Graph drives & lists)

Reuses the Azure app from **section 3** (with `Sites.Read.All` / `Files.Read.All`
consented). SharePoint pulls incrementally via Graph **delta** queries and can
also receive change webhooks.

### 4.1 Find your site + drive/list ids
```bash
python -m connectors.sharepoint.cli test \
  --tenant $MS_TENANT_ID --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET \
  --hostname <contoso>.sharepoint.com --site-path /sites/<SiteName>
# prints the site id and its drive ids
```

### 4.2 (Optional) Create a change subscription
```http
POST https://graph.microsoft.com/v1.0/subscriptions
{
  "changeType": "updated",
  "notificationUrl": "${PUBLIC_URL}/sharepoint/webhook",
  "resource": "drives/<drive-id>/root",
  "expirationDateTime": "<now + 3 days, ISO 8601>",
  "clientState": "<SHAREPOINT_CLIENT_STATE>"
}
```

### 4.3 Configure
```bash
# app creds from section 3 are reused; just add:
export SHAREPOINT_CLIENT_STATE=<secret in 4.2>
# (delta targets — drive/list ids — are deployment-specific; wire them when constructing
#  SharePointConnector(targets=[DriveTarget(...), ListTarget(...)]) or use the backfill CLI)
```

### 4.4 Verify
```bash
python -m connectors.sharepoint.cli backfill \
  --tenant $MS_TENANT_ID --client-id $MS_CLIENT_ID --client-secret $MS_CLIENT_SECRET \
  --drive-id <drive id> --output-dir ./output    # writes sharepoint.jsonl + manifest + metrics
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

# 3) register the push webhooks at ${PUBLIC_URL}/{slack/events,gmail/push,
#    outlook/webhook,sharepoint/webhook}  (sections 1.3, 2.2, 3.3, 4.2)

# 4) open the dashboard, then act in each app
open http://localhost:8001/
#    - Slack: post a message / add a reaction / join a channel
#    - Gmail/Outlook: send a mail (subject "Sign-off notification" → SignOffEvent)
#    - Notion: edit a shared page;   Database: update a row;   SharePoint: edit a file
#    Each change flows ingress → normalizer → bus → L2 store and scrolls live.
```

### Credential reference (all environment variables)

| Connector | Variables |
|---|---|
| Slack | `SLACK_SIGNING_SECRET`, `SLACK_TOKEN` |
| Gmail | `GMAIL_ACCESS_TOKEN`, `GMAIL_PUBSUB_TOKEN` *(or `GMAIL_OIDC_AUDIENCE`)* |
| Outlook | `OUTLOOK_ACCESS_TOKEN` *or* `MS_TENANT_ID`+`MS_CLIENT_ID`+`MS_CLIENT_SECRET`; `OUTLOOK_CLIENT_STATE` |
| SharePoint | `MS_TENANT_ID`+`MS_CLIENT_ID`+`MS_CLIENT_SECRET` *(or `SHAREPOINT_ACCESS_TOKEN`)*; `SHAREPOINT_CLIENT_STATE` |
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
| Outlook/SharePoint 401 on notifications | `*_CLIENT_STATE` env var ≠ the `clientState` set when creating the subscription |
| Notion `list-pages` empty | the integration hasn't been added to any page (section 5.2) |
| Connector missing from `/healthz` | its credentials aren't set → still in fixture mode (expected) |
