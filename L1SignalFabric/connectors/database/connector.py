"""Database connector — generic SQL CDC/outbox pull (the Phase-3 CDCExtractor).

Composes a :class:`~connectors.database.adapter.SqlFetchAdapter` (outbox /
updated-at / mimic) + the change→signal mapper + a watermark, so it resumes
losslessly across restarts (the "0 data loss" criterion). Identical in shape to
the ERP connector, but the adapter is swappable to any SQL source and the cursor
type follows the adapter (int outbox seq or ISO ``updated_at``).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from connectors.common.poller import PollingConnector
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .adapter import Cursor, SqlFetchAdapter
from .mappers import change_to_signal

logger = logging.getLogger("signalfabric.connector.database")


class DatabaseConnector(PollingConnector):
    name = "database"
    source_system = SourceSystem.DATABASE

    def __init__(
        self,
        *,
        tenant_id: str,
        adapter: SqlFetchAdapter,
        watermarks: Optional[WatermarkStore] = None,
        name: Optional[str] = None,
    ) -> None:
        if name:
            self.name = name
        self._adapter = adapter
        super().__init__(tenant_id=tenant_id, start_cursor=adapter.start_cursor(),
                         watermarks=watermarks)

    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one change envelope (used by :meth:`poll`)."""
        return [change_to_signal(raw, self._tenant_id, raw.get("_cursor"))]

    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        out: List[SignalEvent] = []
        records = self._adapter.fetch(self._cursor, limit=limit)
        for rec in records:
            out.append(change_to_signal(rec.data, self._tenant_id, rec.cursor))
            self.commit(rec.cursor)  # advance per row → retried poll is safe
        if records:
            logger.info("database poll: %d rows -> %d signals (cursor=%s)",
                        len(records), len(out), self._cursor)
        return out
