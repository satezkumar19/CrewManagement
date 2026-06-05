"""Pure mapper: one ERP outbox change row -> canonical SignalEvent.

Shared across the mimic and any real adapter. The ERP connector spans three
source systems; the ``table`` field on each row selects which.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.signal import Lineage, Operation, SignalEvent, SourceSystem

# table -> (SourceSystem, entity, primary-key field in row.data)
TABLE_MAP: dict[str, tuple[SourceSystem, str, str]] = {
    "crew": (SourceSystem.CREW_DB, "crew", "crew_id"),
    "contract": (SourceSystem.CONTRACT_CLM, "contract", "contract_id"),
    "vessel_port": (SourceSystem.VESSEL_PORT_DB, "vessel_port", "vessel_id"),
}


def _parse_dt(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def outbox_row_to_signal(row: dict[str, Any], tenant_id: str) -> list[SignalEvent]:
    """Map one outbox row to 0..1 SignalEvents (unknown tables are skipped)."""
    table = row.get("table", "")
    mapping = TABLE_MAP.get(table)
    if mapping is None:
        return []

    source_system, entity, pk_field = mapping
    data = row.get("data", {}) or {}
    seq = row.get("seq")

    return [
        SignalEvent(
            entity=entity,
            key={pk_field: data.get(pk_field)},
            source_system=source_system,
            tenant_id=tenant_id,
            operation=Operation.DELTA,
            data=data,
            timestamp=_parse_dt(row.get("occurred_at")),
            lineage=Lineage(
                extraction_id=f"erp-{table}-{seq}",
                source_endpoint="erp.outbox",
                source_sequence=seq,
            ),
            metadata={"op": row.get("op"), "schemaVersion": "1.0"},
        )
    ]
