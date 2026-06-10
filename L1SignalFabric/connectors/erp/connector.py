"""ERP connector (Day-1 skeleton).

Pull/CDC connector covering the three ERP source systems (Crew DB, Contract/CLM,
Vessel/Port DB) through one transactional-outbox feed. It composes a
``ErpFetchAdapter`` + the shared ``outbox_row_to_signal`` mapper + a watermark,
so it resumes losslessly across restarts (the "50 records, 0 data loss" exit
criterion).

Day-1 scope (this skeleton): the contract, the mimic outbox adapter, watermark
position/commit, and a ``poll`` that maps rows to SignalEvents and advances the
cursor. Day-4 (Sreekumar) swaps in a real Postgres outbox adapter and wires the
poller into a background task.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from core.connector import Checkpoint, EventStreamConnector
from core.signal import SignalEvent, SourceSystem
from core.watermark import InMemoryWatermarkStore, WatermarkStore

from .adapter import ErpFetchAdapter
from .mappers import outbox_row_to_signal

logger = logging.getLogger("signalfabric.connector.erp")


class ErpConnector(EventStreamConnector):
    name = "erp"
    # Representative; emitted events carry their own per-table source_system
    # (CREW_DB / CONTRACT_CLM / VESSEL_PORT_DB).
    source_system = SourceSystem.CREW_DB

    def __init__(
        self,
        *,
        tenant_id: str,
        adapter: ErpFetchAdapter,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._adapter = adapter
        self._wm = watermarks or InMemoryWatermarkStore()
        self._cursor: int = int(self._wm.get(self.name, adapter.start_cursor()))

    # ------------------------------------------------------------ pull contract
    def position(self) -> Checkpoint:
        return self._cursor

    def commit(self, checkpoint: Checkpoint) -> None:
        self._cursor = int(checkpoint)
        self._wm.set(self.name, self._cursor)

    # ---------------------------------------------------------------- ingest
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one outbox row into SignalEvents (used by poll)."""
        return outbox_row_to_signal(raw, self._tenant_id)

    # ----------------------------------------------------------------- poll
    async def poll(self, limit: int | None = None) -> List[SignalEvent]:
        """Fetch new outbox rows, map them, and advance the watermark.

        The cursor advances per row *after* its signals are produced; because
        every SignalEvent carries a stable ``dedup_id`` (source_sequence), a
        retried poll is safe — the bus/sink drop the duplicates.
        """
        out: List[SignalEvent] = []
        records = self._adapter.fetch(self._cursor, limit=limit)
        for rec in records:
            out.extend(await self.ingest(rec.data))
            self.commit(rec.cursor)
        if records:
            logger.info("erp poll: %d rows -> %d signals (cursor=%s)",
                        len(records), len(out), self._cursor)
        return out
