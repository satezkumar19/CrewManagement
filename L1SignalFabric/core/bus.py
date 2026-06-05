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

import logging
from typing import List, Protocol, runtime_checkable

from .signal import SignalEvent

logger = logging.getLogger("signalfabric.bus")


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
