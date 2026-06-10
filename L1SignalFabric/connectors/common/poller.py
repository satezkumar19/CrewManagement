"""Watermark-backed base for pull/CDC connectors.

Factors the position/commit watermark wiring out of the ERP connector so every
pull connector (Notion, SharePoint, Database, Gmail/Outlook backfill) resumes
losslessly across restarts the same way — the "0 data loss" exit criterion.

A subclass implements :meth:`ingest` (raw → SignalEvents) and :meth:`poll`
(fetch new records since :attr:`cursor`, map them, and :meth:`commit` after each
so a retried poll is safe). The cursor type is source-defined: an int outbox
seq, an ISO timestamp, a Gmail ``historyId``, a Graph delta link, etc.
"""

from __future__ import annotations

from typing import Any, List, Optional

from core.connector import Checkpoint, EventStreamConnector
from core.signal import SignalEvent, SourceSystem
from core.watermark import InMemoryWatermarkStore, WatermarkStore


class PollingConnector(EventStreamConnector):
    """Base class for watermark-checkpointed pull connectors."""

    name: str = "polling"
    source_system: SourceSystem

    def __init__(
        self,
        *,
        tenant_id: str,
        start_cursor: Checkpoint,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._wm = watermarks or InMemoryWatermarkStore()
        self._cursor: Checkpoint = self._wm.get(self.name, start_cursor)

    # --- pull contract ---
    @property
    def cursor(self) -> Checkpoint:
        return self._cursor

    def position(self) -> Checkpoint:
        return self._cursor

    def commit(self, checkpoint: Checkpoint) -> None:
        self._cursor = checkpoint
        self._wm.set(self.name, checkpoint)

    # --- subclasses implement these ---
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:  # pragma: no cover
        raise NotImplementedError

    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:  # pragma: no cover
        raise NotImplementedError
