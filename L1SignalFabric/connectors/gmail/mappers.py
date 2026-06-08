"""Pure mappers: Gmail message *metadata* → canonical EMAIL SignalEvent.

Body is never read. A message labelled ``crew/sign-off`` (or whose subject
matches) carries ``l2Intent = CREATE_SIGNOFF_EVENT`` so the L2 sink materializes
a SignOffEvent node — the <5-minute sign-off exit criterion. This unifies the
demo ``email_normalize`` rule with the real Gmail metadata extraction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.common.email import email_record_to_signal, is_sign_off
from core.signal import SignalEvent, SourceSystem


def _split_addresses(value: str) -> List[str]:
    if not value:
        return []
    return [a.strip() for a in value.split(",") if a.strip()]


def _epoch_ms_to_dt(value: Optional[str]) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def message_metadata_to_record(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a Gmail ``messages.get?format=metadata`` payload into a flat dict.

    Returns the same shape the demo email normalizer used, so downstream logic is
    shared. Headers are read from ``payload.headers`` (allow-listed).
    """
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in (msg.get("payload", {}) or {}).get("headers", [])}
    return {
        "message_id": msg.get("id", ""),
        "thread_id": msg.get("threadId"),
        "from": headers.get("from"),
        "to": _split_addresses(headers.get("to", "")),
        "cc": _split_addresses(headers.get("cc", "")),
        "subject": headers.get("subject", ""),
        "labels": msg.get("labelIds", []),
        "sent_at": _epoch_ms_to_dt(msg.get("internalDate")).isoformat(),
        "snippet_present": False,  # explicitly no body / no snippet ingested
    }


def record_to_signal(record: Dict[str, Any], tenant_id: str,
                     source_system: SourceSystem = SourceSystem.GMAIL) -> SignalEvent:
    """Map a flattened Gmail metadata record → EMAIL SignalEvent (shared helper)."""
    return email_record_to_signal(record, tenant_id, source_system,
                                  source_endpoint="/gmail/push", extraction_prefix="gmail")
