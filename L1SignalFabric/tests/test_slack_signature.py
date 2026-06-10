"""Slack HMAC signature verification + reject path through the route."""

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from connectors.slack import verify_slack_signature
from core.bus import LoggingEventBus


def _sign(secret: str, ts: str, body: bytes) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_signature_valid():
    secret, ts, body = "shhh", str(int(time.time())), b'{"a":1}'
    sig = _sign(secret, ts, body)
    res = verify_slack_signature(signing_secret=secret, timestamp=ts, body=body, signature=sig)
    assert res.ok


def test_signature_tampered():
    secret, ts, body = "shhh", str(int(time.time())), b'{"a":1}'
    sig = _sign(secret, ts, b'{"a":2}')  # signed a different body
    res = verify_slack_signature(signing_secret=secret, timestamp=ts, body=body, signature=sig)
    assert not res.ok and "mismatch" in res.reason


def test_signature_stale_timestamp():
    secret, body = "shhh", b'{"a":1}'
    old = str(int(time.time()) - 10_000)
    sig = _sign(secret, old, body)
    res = verify_slack_signature(signing_secret=secret, timestamp=old, body=body, signature=sig)
    assert not res.ok and "stale" in res.reason


def test_route_rejects_unsigned_when_secret_configured():
    # secret set + dev bypass off => unsigned request is 401
    cfg = Settings(slack_signing_secret="shhh", slack_dev_allow_unverified=False)
    app = create_app(settings=cfg, bus=LoggingEventBus())
    client = TestClient(app)
    r = client.post(
        "/slack/events",
        json={"type": "event_callback", "event_id": "E1",
              "event": {"type": "message", "channel": "C", "user": "U", "text": "x", "ts": "1.0"}},
    )
    assert r.status_code == 401
