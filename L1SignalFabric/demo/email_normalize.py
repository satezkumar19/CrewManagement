"""Demo email → SignalEvent normalizer (metadata only).

This is a **demo stand-in** for the real Gmail connector (Day 3). It lets the
generated email stream flow end-to-end today so the sign-off → SignOffEvent story
is demoable on Day 1. It is intentionally NOT under ``connectors/`` — the real
Gmail connector (Pub/Sub push + OIDC verify + history.list) is Sruthy's Day-3
work and will replace this.

It models the one rule that matters for the demo: a ``crew/sign-off`` labelled
(or subject-matched) email carries an ``l2Intent = CREATE_SIGNOFF_EVENT`` so the
L2 sink materializes a SignOffEvent node. Body is never present.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.signal import Lineage, SignalEvent, SourceSystem

_SIGNOFF_SUBJECT = "sign-off notification"


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def is_sign_off(raw: dict[str, Any]) -> bool:
    labels = [str(x).lower() for x in raw.get("labels", [])]
    subject = str(raw.get("subject", "")).lower()
    return "crew/sign-off" in labels or _SIGNOFF_SUBJECT in subject


def email_to_signal(raw: dict[str, Any], tenant_id: str) -> list[SignalEvent]:
    """One Gmail-style metadata record → one EMAIL SignalEvent (metadata only)."""
    message_id = raw.get("message_id", "")
    metadata: dict[str, Any] = {"schemaVersion": "1.0"}
    if is_sign_off(raw):
        metadata["l2Intent"] = "CREATE_SIGNOFF_EVENT"

    return [
        SignalEvent(
            entity="email",
            key={"message_id": message_id},
            source_system=SourceSystem.EMAIL,
            tenant_id=tenant_id,
            data={
                "from": raw.get("from"),
                "to": raw.get("to", []),
                "cc": raw.get("cc", []),
                "subject": raw.get("subject", ""),
                "thread_id": raw.get("thread_id"),
                "sent_at": raw.get("sent_at"),
                "labels": raw.get("labels", []),
                # body intentionally absent — metadata only
            },
            timestamp=_parse_dt(raw.get("sent_at")),
            lineage=Lineage(extraction_id=f"gmail-{message_id}", source_endpoint="/gmail/push"),
            metadata=metadata,
        )
    ]
