.PHONY: install test run smoke seed stream stream-live clean

PY ?= python3
PORT ?= 8001
SPEED ?= 6000

install:
	$(PY) -m pip install -r requirements.txt

# Run the L1 SignalFabric service (Slack ingress + /healthz)
run:
	$(PY) -m uvicorn api.app:app --host 0.0.0.0 --port $(PORT) --reload

test:
	$(PY) -m pytest -q

# Day-1 ingress smoke: boots the app in-process and exercises
# /healthz, the Slack url_verification handshake, a Slack event, and an ERP poll.
smoke:
	$(PY) scripts/smoke.py

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
