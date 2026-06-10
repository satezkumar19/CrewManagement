.PHONY: install test run dashboard up down demo smoke seed stream stream-live clean gmail-authorize gmail-watch gmail-doctor docs outlook-subscribe sharepoint-subscribe outlook-subscriptions sharepoint-subscriptions

PY ?= python3
PORT ?= 8001
SPEED ?= 6000

install:
	$(PY) -m pip install -r requirements.txt

# Run the L1 SignalFabric service (Slack ingress + /healthz + live dashboard)
run:
	$(PY) -m uvicorn api.app:app --host 0.0.0.0 --port $(PORT) --reload

# Same as `run`, but reminds you where the live dashboard is. Open it, click
# "Start live" (needs `make seed` first).
dashboard:
	@echo "Live dashboard → http://localhost:$(PORT)/   (run 'make seed' first if no data)"
	$(PY) -m uvicorn api.app:app --host 0.0.0.0 --port $(PORT)

test:
	$(PY) -m pytest -q

# Day-1 ingress smoke: boots the app in-process and exercises
# /healthz, the Slack url_verification handshake, a Slack event, and an ERP poll.
smoke:
	$(PY) scripts/smoke.py

# --- Demo 1: full pipe (ingress -> normalizer -> bus -> L2 store -> live tail) ---
# Start the server in the background (writes server.log + .server.pid)
up: seed-if-missing
	@nohup $(PY) -m uvicorn api.app:app --host 0.0.0.0 --port $(PORT) > server.log 2>&1 & echo $$! > .server.pid
	@sleep 2 ; echo "L1 SignalFabric up → http://localhost:$(PORT)/  (pid $$(cat .server.pid))"

down:
	@[ -f .server.pid ] && kill $$(cat .server.pid) 2>/dev/null && rm -f .server.pid && echo "stopped" || echo "not running"

# Inject one mock Slack message + one mock sign-off email through the running server
demo:
	$(PY) scripts/demo1.py

seed-if-missing:
	@[ -f data/seed_meta.json ] || $(PY) -m demo.seed --out ./data >/dev/null

# Generate the demo dataset into ./data (backlog + live runway + entities + meta)
seed:
	$(PY) -m demo.seed --out ./data

# Drain the historical backlog through the connectors (fast burst, idempotent)
stream:
	$(PY) -m demo.stream --mode backlog

# Replay the future runway on a virtual clock (the "world in motion")
stream-live:
	$(PY) -m demo.stream --mode live --speed $(SPEED)

# --- Gmail push (Pub/Sub) ---
# Diagnose the whole new-email → Pub/Sub → /gmail/push chain and say which link is broken.
gmail-doctor:
	bash scripts/gmail_push_doctor.sh

# One-time consent → mints a durable GMAIL_REFRESH_TOKEN (prints the trio to paste
# into .env). Needs a "Desktop app" OAuth client (GMAIL_CLIENT_ID/SECRET).
gmail-authorize:
	$(PY) -m connectors.gmail.cli authorize

# (Re)register the Gmail watch — also the RENEWAL command: the watch expires after
# ~7 days, so run this on a schedule (see the cron stanza printed by gmail-doctor).
# Reads the refresh-token trio (GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN) — or a static
# GMAIL_ACCESS_TOKEN — plus GMAIL_PUBSUB_TOPIC from .env.
gmail-watch:
	$(PY) -m connectors.gmail.cli watch

# --- Microsoft Graph push (Outlook + SharePoint change subscriptions) ---
# Register hands-off push: Graph POSTs to MS_WEBHOOK_BASE_URL/<src>/webhook on
# change. The server must be publicly reachable there (Graph validates on create).
# Reads MS_* creds + MS_WEBHOOK_BASE_URL + *_CLIENT_STATE from .env.
outlook-subscribe:
	$(PY) -m connectors.outlook.cli subscribe
sharepoint-subscribe:
	$(PY) -m connectors.sharepoint.cli subscribe

# List / renew status. Graph caps mail + driveItem subscriptions at ~3 days, so
# renew on a schedule: `make outlook-subscriptions` to get the id, then
# `$(PY) -m connectors.outlook.cli renew <id>` (same for sharepoint).
outlook-subscriptions:
	$(PY) -m connectors.outlook.cli subscriptions
sharepoint-subscriptions:
	$(PY) -m connectors.sharepoint.cli subscriptions

# Regenerate the Word docs from their markdown sources (needs pandoc:
# `apt-get install pandoc` / `brew install pandoc`).
docs:
	pandoc docs/CONNECTOR_SETUP.md  -o docs/L1_SignalFabric_Connector_Setup.docx
	pandoc docs/IMPLEMENTATION.md   -o docs/L1_SignalFabric_Implementation.docx
	@echo "regenerated docs/*.docx"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f .erp_watermark.json
