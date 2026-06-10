"""Event bus contract + a Day-1 placeholder implementation.

The ``EventBus`` Protocol is the **agreed seam** between the ingress/connector
track (Sreekumar) and the core/sink track (Sruthy). Connectors and routes depend
only on this Protocol; they never import a concrete bus.

``LoggingEventBus`` is a deliberately trivial placeholder so the ingress side is
runnable and testable on its own before the real bus lands. Sruthy's
``InMemoryBus`` (Day 1) and ``RedisStreamsBus`` (Day 4) implement the same
Protocol and drop in with no change to connectors or routes.
"""

from __future__ import annotations

import inspect
import logging
from collections import Counter, OrderedDict, deque
from typing import Awaitable, Callable, Deque, List, Optional, Protocol, Union, runtime_checkable

from .signal import SignalEvent, utcnow

logger = logging.getLogger("signalfabric.bus")

# A subscriber receives each newly-published (non-duplicate) event. It may be
# sync (e.g. the L2 store's `append`) or async; the bus awaits awaitables.
Subscriber = Callable[[SignalEvent], Union[None, Awaitable[None]]]


class DeadLetterQueue:
    """Durable-ish capture of deliveries that failed after their retries.

    A bounded ring of the events whose subscriber raised, with the error and a
    timestamp — so a poison event is *isolated and recorded* (inspectable via the
    dashboard) rather than silently dropped. The Day-4 ``RedisStreamsBus`` backs
    the same surface with a Redis pending-entries-list / XCLAIM."""

    def __init__(self, maxlen: int = 500) -> None:
        self._items: Deque[dict] = deque(maxlen=maxlen)
        self.total = 0

    def add(self, event: SignalEvent, error: BaseException, *, subscriber: str = "") -> None:
        self.total += 1
        self._items.append({
            "event_id": event.event_id,
            "dedup_id": event.dedup_id[:8],
            "source": event.source_system.value,
            "entity": event.entity,
            "key": event.key,
            "subscriber": subscriber,
            "error": f"{type(error).__name__}: {error}"[:300],
            "ts": utcnow().isoformat(),
        })

    def recent(self, n: int = 100) -> List[dict]:
        return list(self._items)[-n:]

    @property
    def count(self) -> int:
        return self.total


@runtime_checkable
class EventBus(Protocol):
    """Anything a connector can publish a normalized event to."""

    async def publish(self, event: SignalEvent) -> None: ...


class LoggingEventBus:
    """Placeholder bus: logs each event and keeps the last N in memory.

    NOT the production bus — it has no subscribers, ordering, durability, or
    Redis path. It exists only so /slack/events and the connectors can be
    exercised end-to-ingress before Sruthy's InMemoryBus is wired in.
    """

    def __init__(self, keep_last: int = 100) -> None:
        self._keep_last = keep_last
        self.published: List[SignalEvent] = []

    async def publish(self, event: SignalEvent) -> None:
        self.published.append(event)
        if len(self.published) > self._keep_last:
            self.published.pop(0)
        logger.info(
            "[bus-stub] %s/%s key=%s tenant=%s",
            event.source_system.value,
            event.entity,
            event.key,
            event.tenant_id,
        )

    @property
    def count(self) -> int:
        return len(self.published)


class InMemoryBus:
    """The Day-1 production-shaped bus (core track).

    Implements the same ``EventBus`` Protocol the connectors and routes publish
    to, so it drops in via ``create_app(bus=InMemoryBus())`` with no change to
    any connector or route. Beyond the ``LoggingEventBus`` placeholder it adds
    the three properties the downstream actually needs:

      * **idempotency** — duplicates (same ``SignalEvent.dedup_id``) are dropped,
        turning the connectors' at-least-once delivery into exactly-once
        downstream. A bounded LRU of seen ids keeps memory flat.
      * **fan-out to subscribers** — the L2 sink subscribes here
        (``bus.subscribe(store.append)``); a failing subscriber is logged and
        isolated, never dropping the event for the others.
      * **replay** — a bounded ring buffer of recent events so a late subscriber
        (or the SSE viewer) can be brought immediately current.

    Ordering is FIFO: ``publish`` is awaited, fanning out synchronously in
    subscription order. The Day-4 ``RedisStreamsBus`` implements the same
    Protocol + subscribe/replay surface backed by Redis Streams — swap-in by
    construction, no downstream change.
    """

    def __init__(self, *, history: int = 500, dedup_window: int = 50_000,
                 log_buffer: int = 500, subscriber_retries: int = 0) -> None:
        self._subscribers: List[Subscriber] = []
        # retry a failing subscriber this many extra times before dead-lettering
        # (default 0 — preserves the original call-once behaviour); the DLQ keeps
        # any event whose subscriber still fails, so it is never silently lost.
        self._subscriber_retries = subscriber_retries
        self.dlq = DeadLetterQueue()
        self._history: Deque[SignalEvent] = deque(maxlen=history)
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._dedup_window = dedup_window
        # counters (observability — surfaced by stats())
        self.published_count = 0
        self.duplicate_count = 0
        self.by_source: Counter = Counter()
        self.by_entity: Counter = Counter()
        # console log — one line per ingress (publish / dup-drop / subscriber-fail),
        # kept in a bounded ring so the UI can tail it. last_log_line is the most
        # recent entry (a viewer bus reads it after each publish).
        self.log_lines: Deque[dict] = deque(maxlen=log_buffer)
        self.last_log_line: Optional[dict] = None

    def _emit(self, level: str, line: str) -> None:
        entry = {"level": level, "line": line, "ts": utcnow().isoformat()}
        self.last_log_line = entry
        self.log_lines.append(entry)
        getattr(logger, level, logger.info)("[bus] %s", line)

    def recent_log(self, n: int = 200) -> List[dict]:
        """Most recent console log lines (oldest → newest)."""
        return list(self.log_lines)[-n:]

    # --- subscription (the L2 sink registers here) ---
    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    # --- EventBus Protocol ---
    async def publish(self, event: SignalEvent) -> None:
        """Dedup, record, then fan out to subscribers in order.

        Duplicate (already-seen ``dedup_id``) events are counted and dropped
        before fan-out, so each source event reaches the sink exactly once.
        """
        dkey = event.dedup_id
        if dkey in self._seen:
            self.duplicate_count += 1
            self._seen.move_to_end(dkey)  # LRU touch
            self._emit("warning", f"DUP-DROP  {event.source_system.value}/{event.entity} "
                                  f"key={event.key} dedup={dkey[:8]} (#{self.duplicate_count})")
            return

        self._seen[dkey] = None
        if len(self._seen) > self._dedup_window:
            self._seen.popitem(last=False)  # evict oldest

        self._history.append(event)
        self.published_count += 1
        self.by_source[event.source_system.value] += 1
        self.by_entity[event.entity] += 1
        self._emit("info", f"PUBLISH   {event.source_system.value}/{event.entity} "
                           f"key={event.key} dedup={dkey[:8]} -> subs={len(self._subscribers)}")

        for subscriber in list(self._subscribers):
            last_exc: Optional[BaseException] = None
            for attempt in range(self._subscriber_retries + 1):
                try:
                    result = subscriber(event)
                    if inspect.isawaitable(result):
                        await result
                    last_exc = None
                    break
                except Exception as exc:  # a bad subscriber must not lose the event for others
                    last_exc = exc
            if last_exc is not None:
                # isolated + recorded: log, dead-letter, and move on to the next subscriber
                self.dlq.add(event, last_exc,
                             subscriber=getattr(subscriber, "__name__", repr(subscriber)))
                self._emit("error", f"SUBSCRIBER-FAIL {event.event_id} -> DLQ (#{self.dlq.count})")
                logger.exception("[bus] subscriber failed for %s (dead-lettered)", event.event_id)

    # --- replay (bring a late subscriber / viewer current) ---
    def replay(self) -> List[SignalEvent]:
        """Snapshot of recent events, oldest → newest."""
        return list(self._history)

    @property
    def count(self) -> int:
        return self.published_count

    def stats(self) -> dict:
        return {
            "published": self.published_count,
            "duplicates_dropped": self.duplicate_count,
            "subscribers": len(self._subscribers),
            "history_size": len(self._history),
            "dead_letters": self.dlq.count,
            "by_source": dict(self.by_source),
            "by_entity": dict(self.by_entity),
        }
