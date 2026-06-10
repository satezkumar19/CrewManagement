"""HTTP ingress: /healthz, Slack url_verification, Slack event ingest."""

from fastapi.testclient import TestClient

from api.app import create_app
from core.bus import LoggingEventBus
from core.signal import SourceSystem


def _client_with_bus():
    bus = LoggingEventBus()
    app = create_app(bus=bus)
    return TestClient(app), bus


def test_healthz():
    client, _ = _client_with_bus()
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "l1-signalfabric"
    names = {c["name"] for c in body["connectors"]}
    assert {"slack", "erp"} <= names


def test_slack_url_verification_handshake():
    client, _ = _client_with_bus()
    r = client.post(
        "/slack/events",
        json={"type": "url_verification", "challenge": "3eZbrw1aB"},
    )
    assert r.status_code == 200
    assert r.text == "3eZbrw1aB"  # echoed verbatim


def test_slack_message_ingest_publishes_signal():
    client, bus = _client_with_bus()
    payload = {
        "type": "event_callback",
        "event_id": "Ev01",
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel": "C005",
            "user": "U002",
            "text": "hello crew",
            "ts": "1719980964.000100",
        },
    }
    r = client.post("/slack/events", json=payload)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ingested": 1}

    assert bus.count == 1
    ev = bus.published[0]
    assert ev.source_system == SourceSystem.SLACK
    assert ev.entity == "message"
    assert ev.key == {"channel_id": "C005", "ts": "1719980964.000100"}
    assert ev.data["text"] == "hello crew"


def test_slack_duplicate_event_id_deduped():
    client, bus = _client_with_bus()
    payload = {
        "type": "event_callback",
        "event_id": "EvDUP",
        "event": {"type": "message", "channel": "C1", "user": "U1", "text": "x", "ts": "1.0"},
    }
    client.post("/slack/events", json=payload)
    r2 = client.post("/slack/events", json=payload)  # Slack retry
    assert r2.json() == {"ok": True, "ingested": 0}
    assert bus.count == 1


def test_slack_unhandled_event_ignored():
    client, bus = _client_with_bus()
    payload = {
        "type": "event_callback",
        "event_id": "EvX",
        "event": {"type": "channel_archive", "channel": "C1"},
    }
    r = client.post("/slack/events", json=payload)
    assert r.json() == {"ok": True, "ingested": 0}
    assert bus.count == 0
