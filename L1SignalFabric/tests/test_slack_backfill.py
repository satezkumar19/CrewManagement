"""Slack Web-API backfill connector: poll, scrape, user cache, dedup parity."""

import asyncio

from connectors.common import OutputWriter
from connectors.slack import SlackBackfillConfig, SlackBackfillConnector
from connectors.slack.mappers import map_message, message_model_to_signal
from connectors.slack.models import ChannelInfo


class FakeSlackClient:
    api_calls = 7
    rate_limit_hits = 0

    def __init__(self):
        self._history = {
            "C1": [
                {"ts": "1000.0001", "user": "U1", "text": "hello", "reply_count": 1},
                {"ts": "1000.0002", "user": "U2", "text": "world", "reactions":
                    [{"name": "tada", "users": ["U1"], "count": 1}]},
            ]
        }
        self._replies = {("C1", "1000.0001"): [
            {"ts": "1000.0003", "user": "U2", "text": "reply", "thread_ts": "1000.0001"},
        ]}

    def list_channels(self, types="public_channel"):
        return [ChannelInfo(id="C1", name="general", is_member=True, num_members=3)]

    def get_channel_info(self, cid):
        return ChannelInfo(id=cid, name="general", is_member=True)

    def get_channel_history(self, channel_id, oldest=None, latest=None, limit=200):
        for m in self._history.get(channel_id, []):
            if oldest is None or float(m["ts"]) > oldest:
                yield m

    def get_thread_replies(self, channel_id, thread_ts, limit=20):
        return self._replies.get((channel_id, thread_ts), [])

    def get_user_info(self, user_id):
        return {"name": user_id.lower(),
                "profile": {"email": f"{user_id.lower()}@acme.io", "display_name": user_id}}


def _connector():
    return SlackBackfillConnector(tenant_id="t", client=FakeSlackClient(),
                                  config=SlackBackfillConfig(channels="all"))


def test_poll_emits_and_advances_watermark():
    c = _connector()
    sigs = asyncio.run(c.poll())
    assert [s.entity for s in sigs] == ["message", "message"]
    assert sigs[0].data["user_email"] == "u1@acme.io"
    # watermark advanced to newest ts
    assert c.position() == "1000.000200"
    # second poll: nothing new
    assert asyncio.run(c.poll()) == []


def test_scrape_writes_messages_and_threads(tmp_path):
    c = _connector()
    w = OutputWriter(str(tmp_path), source="slack", entity="messages")
    metrics = c.scrape(writer=w)
    # 2 channel messages + 1 thread reply
    assert metrics.records_total == 3
    assert metrics.extra["channels"]["successful"] == 1
    assert metrics.extra["messages"]["with_threads"] == 1
    assert metrics.extra["users"]["email_resolved"] >= 1
    assert (tmp_path / "slack.jsonl").exists()
    assert (tmp_path / "manifest.json").exists()


def test_backfill_dedup_matches_live_mapper():
    """A backfilled message and a live Events-API message dedup to one identity."""
    c = _connector()
    sigs = asyncio.run(c.poll())
    backfilled = sigs[0]  # channel C1 ts 1000.0001
    live = map_message({"channel": "C1", "ts": "1000.0001", "user": "U1", "text": "hello"},
                       {"event_id": "Ev1"}, "t")
    assert backfilled.dedup_id == live.dedup_id


def test_user_cache_hits():
    c = _connector()
    asyncio.run(c.poll())
    stats = c.user_cache.get_stats()
    assert stats["api_lookups"] == 2  # U1, U2 resolved once each
    assert stats["cached_users"] == 2
