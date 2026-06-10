"""BroadcastBus fan-out + dashboard / SSE routes."""

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.app import create_app
from api.live import BroadcastBus
from core.signal import SignalEvent, SourceSystem


def _event(signoff: bool = False) -> SignalEvent:
    md = {"l2Intent": "CREATE_SIGNOFF_EVENT"} if signoff else {}
    return SignalEvent(
        entity="message", key={"channel_id": "C1", "ts": "1.0"},
        source_system=SourceSystem.SLACK, tenant_id="t",
        data={"text": "hello"}, timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
        metadata=md,
    )


def test_broadcastbus_fans_out_and_counts():
    async def run():
        bus = BroadcastBus()
        q = bus.subscribe()
        await bus.publish(_event())
        await bus.publish(_event(signoff=True))
        # /stream multiplexes two payload kinds: "signal" rows and "buslog" lines
        # (the InMemoryBus console tap). Partition the drained queue by type.
        drained = []
        for _ in range(8):
            try:
                drained.append(await asyncio.wait_for(q.get(), 0.5))
            except asyncio.TimeoutError:
                break
        signals = [d for d in drained if d.get("type") == "signal"]
        buslog = [d for d in drained if d.get("type") == "buslog"]
        assert signals[0]["source"] == "SLACK" and signals[0]["summary"] == "hello"
        assert signals[1]["signoff"] is True
        assert bus.total == 2 and bus.signoff == 1
        assert bus.totals()["by_source"]["SLACK"] == 2
        # the InMemoryBus tap produced console lines for each ingress
        assert buslog and any("PUBLISH" in b["line"] for b in buslog)
    asyncio.run(run())


def test_buslog_endpoint_reports_lines_and_stats():
    client = TestClient(create_app())
    client.post("/demo/inject")          # pushes events through the tapped InMemoryBus
    body = client.get("/bus/log").json()
    assert body["stats"]["published"] >= 2
    assert any("PUBLISH" in ln["line"] for ln in body["lines"])


def test_dashboard_served():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "L1 SignalFabric" in r.text
    assert "/stream" in r.text  # the page wires up the SSE feed


def test_demo_status_idle():
    client = TestClient(create_app())
    r = client.get("/demo/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["totals"]["total"] == 0
