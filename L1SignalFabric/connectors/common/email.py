"""Canonical e-mail metadata → SignalEvent mapping (shared Gmail/Outlook).

Both e-mail providers normalize to the same flat metadata record
(``from``/``to``/``cc``/``subject``/``thread_id``/``sent_at``/``labels``) and the
same sign-off rule, so the record→SignalEvent step lives here once. Body is never
present in the record — these are metadata connectors by design.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.signal import Lineage, SignalEvent, SourceSystem

SIGNOFF_SUBJECT = "sign-off notification"
SIGNOFF_LABEL = "crew/sign-off"


def is_sign_off(record: Dict[str, Any]) -> bool:
    labels = [str(x).lower() for x in record.get("labels", [])]
    subject = str(record.get("subject", "")).lower()
    return SIGNOFF_LABEL in labels or SIGNOFF_SUBJECT in subject


def _parse_dt(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def email_record_to_signal(
    record: Dict[str, Any],
    tenant_id: str,
    source_system: SourceSystem,
    *,
    source_endpoint: str,
    extraction_prefix: str,
) -> SignalEvent:
    message_id = record.get("message_id", "")
    metadata: Dict[str, Any] = {"schemaVersion": "1.0"}
    if is_sign_off(record):
        metadata["l2Intent"] = "CREATE_SIGNOFF_EVENT"
    return SignalEvent(
        entity="email",
        key={"message_id": message_id},
        source_system=source_system,
        tenant_id=tenant_id,
        data={
            "from": record.get("from"),
            "to": record.get("to", []),
            "cc": record.get("cc", []),
            "subject": record.get("subject", ""),
            "thread_id": record.get("thread_id"),
            "sent_at": record.get("sent_at"),
            "labels": record.get("labels", []),
            # body intentionally absent — metadata only
        },
        timestamp=_parse_dt(record.get("sent_at")),
        lineage=Lineage(extraction_id=f"{extraction_prefix}-{message_id}",
                        source_endpoint=source_endpoint),
        metadata=metadata,
    )
