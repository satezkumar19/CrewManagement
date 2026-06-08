"""Pure mapper: one DB change envelope → canonical DATABASE SignalEvent.

Generalizes the ERP outbox mapper: the ``table`` field selects the ``entity``;
the adapter has already extracted the natural ``key``. Operation is always DELTA
(L1 streams), with the original SQL op (INSERT/UPDATE/DELETE) preserved in
``metadata.op``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.signal import Lineage, Operation, SignalEvent, SourceSystem


def _parse_dt(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def change_to_signal(envelope: Dict[str, Any], tenant_id: str,
                     cursor: Any = None) -> SignalEvent:
    table = envelope.get("table", "row")
    return SignalEvent(
        entity=table,
        key=envelope.get("key", {}) or {},
        source_system=SourceSystem.DATABASE,
        tenant_id=tenant_id,
        operation=Operation.DELTA,
        data=envelope.get("row", {}) or {},
        timestamp=_parse_dt(envelope.get("occurred_at")),
        lineage=Lineage(
            extraction_id=f"db-{table}-{cursor}",
            source_endpoint="db.cdc",
            source_sequence=cursor if isinstance(cursor, int) else None,
        ),
        metadata={"op": envelope.get("op"), "schemaVersion": "1.0"},
    )
