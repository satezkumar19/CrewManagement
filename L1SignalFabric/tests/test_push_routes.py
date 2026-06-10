"""HTTP ingress for the new push connectors: Gmail, Outlook, SharePoint."""

import base64
import json

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from core.bus import InMemoryBus


def _client(settings=None):
    bus = InMemoryBus()
    app = create_app(bus=bus, settings=settings)
    return TestClient(app), bus


def test_health_lists_all_push_connectors():
    client, _ = _client()
    names = {c["name"] for c in client.get("/healthz").json()["connectors"]}
    assert {"slack", "gmail", "outlook", "sharepoint", "database"} <= names


def test_gmail_push_publishes_email_signal():
    client, bus = _client()
    notif = {"historyId": "9", "_messages": [
        {"message_id": "m1", "from": "a@x", "to": ["b@y"],
         "subject": "Sign-off notification", "labels": [], "sent_at": "2024-06-01T10:00:00Z"}]}
    env = {"message": {"data": base64.b64encode(json.dumps(notif).encode()).decode(),
                       "messageId": "p1"}}
    r = client.post("/gmail/push", json=env)
    assert r.status_code == 200 and r.json()["ingested"] == 1
    assert bus.by_source["GMAIL"] == 1
    assert bus.published_count == 1


def test_outlook_validation_handshake():
    client, _ = _client()
    r = client.post("/outlook/webhook?validationToken=HELLO", content=b"")
    assert r.status_code == 200 and r.text == "HELLO"


def test_sharepoint_validation_handshake():
    client, _ = _client()
    r = client.post("/sharepoint/webhook?validationToken=SP", content=b"")
    assert r.status_code == 200 and r.text == "SP"


def test_outlook_client_state_rejected():
    client, _ = _client(Settings(outlook_client_state="kept"))
    r = client.post("/outlook/webhook", json={"value": [{"clientState": "wrong"}]})
    assert r.status_code == 401


def test_outlook_webhook_ingests_inline_message():
    client, bus = _client()
    body = {"value": [{"subscriptionId": "s1", "resourceData": {"id": "m1"},
                       "_message": {"internetMessageId": "<m1>", "conversationId": "c1",
                                    "from": {"emailAddress": {"address": "a@x"}},
                                    "toRecipients": [{"emailAddress": {"address": "b@y"}}],
                                    "subject": "Hi", "categories": [],
                                    "receivedDateTime": "2024-06-01T10:00:00Z"}}]}
    r = client.post("/outlook/webhook", json=body)
    assert r.status_code == 200 and r.json()["ingested"] == 1
    assert bus.by_source["OUTLOOK"] == 1
