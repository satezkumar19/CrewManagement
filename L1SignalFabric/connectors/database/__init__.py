"""Database connector — generic SQL CDC/outbox pull (Phase-3 CDCExtractor).

The ERP connector specialized to one in-memory outbox; this one is the general
form: swap the :class:`SqlFetchAdapter` (outbox table, updated-at high-watermark,
or in-memory mimic) and stream any SQL source's changes as DATABASE DELTAs.
"""

from .adapter import (
    InMemoryOutboxAdapter,
    OutboxAdapter,
    RawRecord,
    SqlFetchAdapter,
    UpdatedAtAdapter,
)
from .connector import DatabaseConnector
from .mappers import change_to_signal

__all__ = [
    "DatabaseConnector",
    "SqlFetchAdapter",
    "OutboxAdapter",
    "UpdatedAtAdapter",
    "InMemoryOutboxAdapter",
    "RawRecord",
    "change_to_signal",
]
