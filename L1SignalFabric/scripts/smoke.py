"""Day-1 ingress smoke test (no external services).

Boots the app in-process with the placeholder bus and exercises Sreekumar's
Day-1 deliverables end-to-ingress:

  1. GET  /healthz
  2. POST /slack/events  (url_verification handshake)
  3. POST /slack/events  (a real message event_callback)
  4. ERP outbox poll -> SignalEvents

Run: `python scripts/smoke.py`  (or `make smoke`)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# allow running as a plain script (add project root to path)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from connectors.erp import ErpConnector, InMemoryOutboxAdapter  # noqa: E402
from core.bus import LoggingEventBus  # noqa: E402


def main() -> int:
    bus = LoggingEventBus()
    app = create_app(bus=bus)
    client = TestClient(app)

    print("1) /healthz")
    r = client.get("/healthz")
    print("   ->", r.status_code, r.json())
    assert r.status_code == 200 and r.json()["status"] == "ok"

    print("2) /slack/events  url_verification")
    r = client.post("/slack/events", json={"type": "url_verification", "challenge": "abc123"})
    print("   ->", r.status_code, repr(r.text))
    assert r.status_code == 200 and r.text == "abc123"

    print("3) /slack/events  message event_callback")
    payload = {
        "type": "event_callback",
        "event_id": "Ev0SMOKE01",
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel": "C005",
            "user": "U002",
            "text": "Vessel ETA updated for voyage V-417",
            "ts": "1719980964.000100",
        },
    }
    r = client.post("/slack/events", json=payload)
    print("   ->", r.status_code, r.json())
    assert r.json() == {"ok": True, "ingested": 1}

    print("4) ERP outbox poll")
    outbox = InMemoryOutboxAdapter()
    outbox.append(table="crew", op="update", occurred_at="2026-06-08T09:00:00Z",
                  data={"crew_id": "C-1001", "name": "A. Rao", "rank": "2/O", "status": "onboard"})
    erp = ErpConnector(tenant_id="maritime-acme", adapter=outbox)
    signals = asyncio.run(erp.poll())
    print("   ->", len(signals), "signal(s):",
          [(s.source_system.value, s.entity, s.key) for s in signals])
    assert len(signals) == 1 and signals[0].source_system.value == "CREW_DB"

    print(f"\nbus captured {bus.count} event(s) via ingress — Day-1 ingress OK ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
