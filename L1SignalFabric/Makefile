.PHONY: install test run dashboard up down demo smoke seed stream stream-live clean

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

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f .erp_watermark.json
