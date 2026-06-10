"""Shared connector infra: rate-limit/retry HTTP, secrets, writer, metrics."""

import json

import pytest

from connectors.common import (
    OutputWriter,
    RateLimitedClient,
    RateLimitError,
    ScrapeMetrics,
    parse_timestamp,
)
from connectors.common.http import HTTPError
from core.signal import SignalEvent, SourceSystem
from datetime import datetime, timezone


class _Resp:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text
        self.reason = "err"

    def json(self):
        return self._body


class _FakeSession:
    """Replays a scripted list of responses, recording calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1
        return self._responses.pop(0)


def _client(responses):
    c = RateLimitedClient(base_url="https://x", rate_limit_delay_ms=0,
                          max_rate_limit_errors=2, sleep=lambda *_: None)
    c._session = _FakeSession(responses)
    return c


def test_success_returns_json_and_counts_calls():
    c = _client([_Resp(200, {"ok": True})])
    assert c.get("m")["ok"] is True
    assert c.api_calls == 1


def test_429_retries_then_succeeds_and_resets():
    c = _client([_Resp(429, headers={"Retry-After": "0"}), _Resp(200, {"ok": True})])
    assert c.get("m")["ok"] is True
    assert c.rate_limit_hits == 1
    assert c._consecutive_rate_limits == 0  # reset on success


def test_429_exhausts_to_rate_limit_error():
    c = _client([_Resp(429, headers={"Retry-After": "0"}),
                 _Resp(429, headers={"Retry-After": "0"})])
    with pytest.raises(RateLimitError):
        c.get("m")


def test_5xx_backs_off_then_raises_after_retries():
    c = RateLimitedClient(base_url="https://x", rate_limit_delay_ms=0,
                          max_server_retries=1, sleep=lambda *_: None)
    c._session = _FakeSession([_Resp(500), _Resp(503)])
    with pytest.raises(HTTPError):
        c.get("m")


def test_4xx_raises_http_error():
    c = _client([_Resp(404, {"error": "nope"})])
    with pytest.raises(HTTPError) as ei:
        c.get("m")
    assert ei.value.status == 404


def test_paginate_follows_cursor():
    pages = [
        _Resp(200, {"items": [1, 2], "next": "c1"}),
        _Resp(200, {"items": [3], "next": None}),
    ]
    c = _client(pages)
    got = list(c.paginate("m", items_key="items", next_param="cursor",
                          next_from=lambda p: p.get("next")))
    assert got == [1, 2, 3]


@pytest.mark.parametrize("value,expected", [
    ("2024-01-01", "2024-01-01T00:00:00+00:00"),
    ("2024-01-01T12:30:00Z", "2024-01-01T12:30:00+00:00"),
    (0, "1970-01-01T00:00:00+00:00"),
])
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value).isoformat() == expected


def test_parse_timestamp_none():
    assert parse_timestamp(None) is None


def test_writer_emits_jsonl_manifest_metrics(tmp_path):
    w = OutputWriter(str(tmp_path), source="slack", entity="messages")
    ev = SignalEvent(entity="message", key={"id": 1}, source_system=SourceSystem.SLACK,
                     tenant_id="t", timestamp=datetime.now(timezone.utc))
    with w:
        w.write_event(ev)
    assert w.count == 1
    lines = (tmp_path / "slack.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["entity"] == "message"

    m = ScrapeMetrics(); m.records_total = 1; m.finalize()
    w.write_manifest(SourceSystem.SLACK.value, 1)
    w.write_metrics(m)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["version"] == "2.0"
    assert manifest["files"][0]["sourceSystem"] == "SLACK"
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["records"]["total"] == 1
