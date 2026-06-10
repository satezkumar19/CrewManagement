"""InMemoryBus (core track): dedup, subscriber fan-out, replay, and the
end-to-end seam where the L2 sink subscribes to it via create_app(bus=...)."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.bus import InMemoryBus, EventBus
from core.signal import Lineage, Operation, SignalEvent, SourceSystem


def _ev(seq: int, *, entity="message", source=SourceSystem.SLACK, **data) -> SignalEvent:
    return SignalEvent(
        entity=entity, key={"id": seq}, source_system=source, tenant_id="t",
        operation=Operation.DELTA, data=data,
        timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
        lineage=Lineage(extraction_id="x", source_sequence=seq),
    )


def test_satisfies_eventbus_protocol():
    assert isinstance(InMemoryBus(), EventBus)


def test_fanout_to_subscribers_in_order():
    bus = InMemoryBus()
    seen_a, seen_b = [], []
    bus.subscribe(lambda e: seen_a.append(e.event_id))
    bus.subscribe(lambda e: seen_b.append(e.event_id))

    asyncio.run(bus.publish(_ev(1)))
    asyncio.run(bus.publish(_ev(2)))

    assert len(seen_a) == 2 and seen_a == seen_b
    assert bus.count == 2


def test_async_subscriber_is_awaited():
    bus = InMemoryBus()
    got = []

    async def asink(e):
        got.append(e.entity)

    bus.subscribe(asink)
    asyncio.run(bus.publish(_ev(1, entity="reaction")))
    assert got == ["reaction"]


def test_duplicates_are_dropped():
    bus = InMemoryBus()
    delivered = []
    bus.subscribe(lambda e: delivered.append(e))

    async def go():
        await bus.publish(_ev(1))
        await bus.publish(_ev(1))   # identical source event → same dedup_id
        await bus.publish(_ev(2))

    asyncio.run(go())
    assert len(delivered) == 2                 # the duplicate never reached the sink
    assert bus.stats()["duplicates_dropped"] == 1
    assert bus.stats()["published"] == 2


def test_failing_subscriber_is_isolated():
    bus = InMemoryBus()
    good = []
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(lambda e: good.append(e))

    asyncio.run(bus.publish(_ev(1)))
    assert len(good) == 1                       # the bad subscriber didn't lose the event


def test_replay_returns_recent_history():
    bus = InMemoryBus(history=3)

    async def go():
        for i in range(5):
            await bus.publish(_ev(i))

    asyncio.run(go())
    replayed = bus.replay()
    assert len(replayed) == 3                   # bounded ring buffer
    assert [e.key["id"] for e in replayed] == [2, 3, 4]


def test_l2_sink_subscribes_via_create_app(tmp_path):
    """create_app(bus=InMemoryBus()) wires the L2 store as a subscriber — the
    documented seam — and the Slack route publishes through it into L2."""
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(tmp_path / "l2.jsonl")

    bus = InMemoryBus()
    client = TestClient(create_app(settings=cfg, bus=bus))

    r = client.post("/slack/events", json={
        "type": "event_callback", "event_id": "Ev01", "team_id": "T1",
        "event": {"type": "message", "channel": "C1", "user": "U1",
                  "text": "hi", "ts": "1719980964.000100"},
    })
    assert r.status_code == 200 and r.json().get("ingested") == 1
    assert bus.count == 1
    # the event was projected into the L2 store by the subscribed sink
    assert client.app.state.l2_store.counts()["total"] == 1
