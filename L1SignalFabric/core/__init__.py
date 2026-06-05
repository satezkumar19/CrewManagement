"""L1 SignalFabric core contracts (shared, agreed Day 1).

Connectors (ingress track) and the bus/sink (core track) both build against
these types. Keep this layer dependency-light and wire-compatible with Conduit.
"""

from .bus import EventBus, LoggingEventBus
from .connector import (
    Checkpoint,
    EventStreamConnector,
    InboundRequest,
    VerifyOutcome,
    VerifyResult,
)
from .dedup import dedup_key
from .signal import Lineage, Operation, SignalEvent, SourceSystem, utcnow
from .watermark import (
    Cursor,
    FileWatermarkStore,
    InMemoryWatermarkStore,
    WatermarkStore,
)

__all__ = [
    # signal
    "SignalEvent",
    "SourceSystem",
    "Operation",
    "Lineage",
    "utcnow",
    # connector
    "EventStreamConnector",
    "InboundRequest",
    "VerifyResult",
    "VerifyOutcome",
    "Checkpoint",
    # bus
    "EventBus",
    "LoggingEventBus",
    # dedup
    "dedup_key",
    # watermark
    "WatermarkStore",
    "InMemoryWatermarkStore",
    "FileWatermarkStore",
    "Cursor",
]
