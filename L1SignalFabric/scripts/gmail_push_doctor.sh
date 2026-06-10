#!/usr/bin/env bash
# Gmail → Pub/Sub → /gmail/push chain doctor.
#
# Walks every link that has to be healthy for a *new email* to auto-trigger the
# connector, and tells you which one is broken. A manual POST from the OAuth
# Playground only exercises the LAST link — this checks the whole chain.
#
#   chain:  new email → users.watch (active) → Pub/Sub topic
#                     → PUSH subscription → POST /gmail/push?token=… → ingest
#
# Reads config from the environment (and a .env if present). Needs `gcloud`
# (authenticated to the project that owns the topic), `curl`, and `jq` optional.
#
# Usage:
#   scripts/gmail_push_doctor.sh
#   GMAIL_PUBSUB_TOPIC=projects/p/topics/t GMAIL_PUSH_ENDPOINT=https://x/gmail/push?token=s \
#     scripts/gmail_push_doctor.sh
#
# Note: an *active watch* cannot be queried via gcloud (Gmail exposes no
# "list watches" API). The doctor checks everything queryable and then tells you
# how to (re)assert the watch — see `cli.py watch` / `make gmail-watch`.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
ok()   { echo "  ${GREEN}✓${OFF} $*"; }
bad()  { echo "  ${RED}✗${OFF} $*"; FAILED=1; }
warn() { echo "  ${YEL}!${OFF} $*"; }
hint() { echo "    ${DIM}↳ $*${OFF}"; }
FAILED=0

TOPIC="${GMAIL_PUBSUB_TOPIC:-}"
TOKEN="${GMAIL_PUBSUB_TOKEN:-}"
ENDPOINT="${GMAIL_PUSH_ENDPOINT:-}"
PUSH_SA="gmail-api-push@system.gserviceaccount.com"

echo "Gmail push doctor — ${ROOT}"
echo

# --- 0. prerequisites ---
echo "0) prerequisites"
if command -v gcloud >/dev/null 2>&1; then
  ACCT=$(gcloud config get-value account 2>/dev/null)
  PROJ=$(gcloud config get-value project 2>/dev/null)
  ok "gcloud present (account=${ACCT:-?} project=${PROJ:-?})"
else
  warn "gcloud not found — Pub/Sub-side checks (topic/IAM/subscription) will be skipped"
  hint "install the Cloud SDK + 'gcloud auth login', or run those checks on a box that has it"
fi
command -v curl >/dev/null 2>&1 && ok "curl present" || bad "curl not found"
echo

# --- 1. config sanity ---
echo "1) config"
if [ -z "$TOPIC" ]; then
  bad "GMAIL_PUBSUB_TOPIC not set"
  hint "expected projects/<PROJECT_ID>/topics/<TOPIC_ID>"
elif [[ "$TOPIC" == *"oauth-2-playground"* ]]; then
  bad "topic lives in google.com:oauth-2-playground — watch can NEVER register here"
  hint "create the topic in YOUR project and mint the OAuth token from YOUR client"
else
  ok "GMAIL_PUBSUB_TOPIC=$TOPIC"
fi
# parse projects/<p>/topics/<t>
TPROJECT="${TOPIC#projects/}"; TPROJECT="${TPROJECT%%/*}"
TNAME="${TOPIC##*/}"
[ -n "$TOKEN" ] && ok "GMAIL_PUBSUB_TOKEN set (shared-secret push auth)" \
                || warn "GMAIL_PUBSUB_TOKEN empty — pushes accepted only via OIDC or dev bypass"
[ -n "$ENDPOINT" ] && ok "GMAIL_PUSH_ENDPOINT=$ENDPOINT" \
                   || warn "GMAIL_PUSH_ENDPOINT not set — skipping subscription-endpoint match + reachability"
# The server gets a live Gmail client from EITHER the self-refreshing OAuth
# trio (preferred) OR a static GMAIL_ACCESS_TOKEN (see _build_gmail_client in
# api/app.py). Without one, push ingest expands 0 (no client to read history).
if [ -n "${GMAIL_CLIENT_ID:-}" ] && [ -n "${GMAIL_CLIENT_SECRET:-}" ] && [ -n "${GMAIL_REFRESH_TOKEN:-}" ]; then
  ok "GMAIL refresh-token trio set (server mints fresh access tokens; durable history expansion)"
elif [ -n "${GMAIL_ACCESS_TOKEN:-}" ]; then
  ok "GMAIL_ACCESS_TOKEN set (server can expand history on push; static, ~1h)"
else
  warn "no Gmail server credential — real pushes will ingest 0 (no client to expand history)"
  hint "set GMAIL_CLIENT_ID+GMAIL_CLIENT_SECRET+GMAIL_REFRESH_TOKEN (run: make gmail-authorize)"
  hint "or a static GMAIL_ACCESS_TOKEN — so GmailConnector gets a live client (api/app.py)"
fi
echo

# --- 2. topic exists ---
echo "2) Pub/Sub topic"
if command -v gcloud >/dev/null 2>&1 && [ -n "$TNAME" ]; then
  if gcloud pubsub topics describe "$TNAME" --project "$TPROJECT" >/dev/null 2>&1; then
    ok "topic exists: $TOPIC"
  else
    bad "topic not found (or no access): $TOPIC"
    hint "gcloud pubsub topics create $TNAME --project $TPROJECT"
  fi
else
  warn "skipped (need gcloud + topic)"
fi
echo

# --- 3. publisher IAM for Gmail's service account ---
echo "3) topic IAM — Gmail publisher rights"
if command -v gcloud >/dev/null 2>&1 && [ -n "$TNAME" ]; then
  POLICY=$(gcloud pubsub topics get-iam-policy "$TNAME" --project "$TPROJECT" \
             --format=json 2>/dev/null)
  if echo "$POLICY" | grep -q "$PUSH_SA" && echo "$POLICY" | grep -q "roles/pubsub.publisher"; then
    ok "$PUSH_SA has roles/pubsub.publisher"
  else
    bad "$PUSH_SA is NOT a publisher on the topic — users.watch() will fail"
    hint "gcloud pubsub topics add-iam-policy-binding $TNAME --project $TPROJECT \\"
    hint "  --member=serviceAccount:$PUSH_SA --role=roles/pubsub.publisher"
  fi
else
  warn "skipped (need gcloud + topic)"
fi
echo

# --- 4. push subscription delivering to our endpoint ---
echo "4) push subscription → /gmail/push"
if command -v gcloud >/dev/null 2>&1 && [ -n "$TNAME" ]; then
  SUBS=$(gcloud pubsub subscriptions list --project "$TPROJECT" --format=json 2>/dev/null)
  # subscriptions on THIS topic with a non-empty push endpoint
  MATCH=$(echo "$SUBS" | tr -d '\n' | grep -o '{[^{]*"pushConfig"[^}]*}[^}]*}' || true)
  EPS=$(echo "$SUBS" | grep -o '"pushEndpoint": *"[^"]*"' | sed 's/.*"pushEndpoint": *"//;s/"$//')
  if [ -z "$EPS" ]; then
    bad "no PUSH subscription found in $TPROJECT — Pub/Sub will never call your URL"
    hint "a PULL subscription won't deliver. Create a push one:"
    hint "gcloud pubsub subscriptions create gmail-push --project $TPROJECT \\"
    hint "  --topic=$TNAME --push-endpoint='${ENDPOINT:-https://<host>/gmail/push?token=<secret>}'"
  else
    echo "$EPS" | while read -r ep; do hint "found push endpoint: $ep"; done
    if [ -n "$ENDPOINT" ] && echo "$EPS" | grep -qF "$ENDPOINT"; then
      ok "a subscription targets exactly GMAIL_PUSH_ENDPOINT"
    elif [ -n "$ENDPOINT" ]; then
      bad "no subscription matches GMAIL_PUSH_ENDPOINT ($ENDPOINT)"
      hint "ngrok-free URLs rotate on restart — update the subscription's --push-endpoint"
    else
      warn "set GMAIL_PUSH_ENDPOINT to verify the endpoint matches"
    fi
  fi
else
  warn "skipped (need gcloud + topic)"
fi
echo

# --- 5. endpoint reachability (the last link) ---
echo "5) endpoint reachability"
if [ -n "$ENDPOINT" ] && command -v curl >/dev/null 2>&1; then
  # an empty Pub/Sub-shaped POST: verify passes, ingest yields 0 — proves transport+auth.
  # capture body + status in one shot (no temp file → avoids curl 'failed writing body').
  RESP=$(curl -s -m 15 -X POST -H 'Content-Type: application/json' \
           --data '{}' -w $'\n%{http_code}' "$ENDPOINT")
  CODE="${RESP##*$'\n'}"
  BODY="${RESP%$'\n'*}"
  if [ "$CODE" = 200 ]; then
    ok "POST $ENDPOINT → 200  ${DIM}$BODY${OFF}"
  elif [ "$CODE" = 401 ]; then
    bad "POST → 401 unauthorized — token mismatch (?token= vs GMAIL_PUBSUB_TOKEN)  ${DIM}$BODY${OFF}"
  else
    bad "POST → HTTP $CODE  ${DIM}$BODY${OFF}"
    hint "is the server up and the ngrok tunnel live?"
  fi
else
  warn "skipped (need GMAIL_PUSH_ENDPOINT + curl)"
fi
echo

# --- 6. the watch (not queryable) ---
echo "6) users.watch (active?)"
warn "Gmail exposes no 'list watches' API — cannot verify from here."
hint "Assert it (idempotent; also use this to RENEW before the 7-day expiry):"
hint "  python -m connectors.gmail.cli watch --token <ACCESS_TOKEN> --topic $TOPIC"
hint "  or: make gmail-watch"
hint "Expect 'watch registered: historyId=… expiration=…'. The expiration is ~7"
hint "days out — re-run before then (see cron stanza in scripts/gmail_push_doctor.sh)."
echo

echo "${DIM}-- watch renewal (cron) ----------------------------------------------${OFF}"
echo "${DIM}# renew daily at 03:00; watch lasts 7d, so daily gives generous slack${OFF}"
echo "${DIM}0 3 * * *  cd $ROOT && make gmail-watch >> /tmp/gmail-watch.log 2>&1${OFF}"
echo

if [ "$FAILED" = 1 ]; then
  echo "${RED}Chain has at least one broken link (see ✗ above).${OFF}"
  exit 1
fi
echo "${GREEN}All queryable links healthy. If email still doesn't trigger, (re)assert the watch (step 6).${OFF}"
