"""Pure mappers: Gmail message → canonical EMAIL SignalEvent.

When the message was fetched with ``format=full`` (server ``EMAIL_INGEST_BODY``
on), the decoded body is lifted into the record so downstream L2 sees the
content; a ``format=metadata`` payload simply carries no body and the record's
``body`` is empty. A message labelled ``crew/sign-off`` (or whose subject
matches) carries ``l2Intent = CREATE_SIGNOFF_EVENT`` so the L2 sink materializes
a SignOffEvent node — the <5-minute sign-off exit criterion. This unifies the
demo ``email_normalize`` rule with the real Gmail extraction.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, List, Optional

from connectors.common.email import email_record_to_signal, is_sign_off
from core.signal import SignalEvent, SourceSystem

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]*\n[ \t]*")


def _split_addresses(value: str) -> List[str]:
    if not value:
        return []
    return [a.strip() for a in value.split(",") if a.strip()]


def _epoch_ms_to_dt(value: Optional[str]) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _decode_b64url(data: Optional[str]) -> str:
    if not data:
        return ""
    try:
        pad = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _html_to_text(html: str) -> str:
    return unescape(_TAG.sub(" ", html))


def _walk_part(part: Dict[str, Any], mime: str) -> str:
    """Depth-first search a Gmail payload tree for the first body of ``mime``."""
    if part.get("mimeType") == mime:
        text = _decode_b64url((part.get("body") or {}).get("data"))
        if text:
            return text
    for sub in part.get("parts") or []:
        found = _walk_part(sub, mime)
        if found:
            return found
    return ""


def _extract_body(payload: Dict[str, Any]) -> str:
    """Prefer text/plain; fall back to a tag-stripped text/html part."""
    plain = _walk_part(payload, "text/plain")
    if plain:
        return plain.strip()
    html = _walk_part(payload, "text/html")
    return _html_to_text(html).strip() if html else ""


def message_metadata_to_record(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a Gmail ``messages.get`` payload into a flat dict.

    Headers are read from ``payload.headers`` (allow-listed). When the payload was
    fetched with ``format=full`` the body is decoded from the MIME parts (text or
    HTML); a ``format=metadata`` payload yields an empty ``body``.
    """
    payload = msg.get("payload", {}) or {}
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("headers", [])}
    body = _extract_body(payload)
    return {
        "message_id": msg.get("id", ""),
        "thread_id": msg.get("threadId"),
        "from": headers.get("from"),
        "to": _split_addresses(headers.get("to", "")),
        "cc": _split_addresses(headers.get("cc", "")),
        "subject": headers.get("subject", ""),
        "labels": msg.get("labelIds", []),
        "sent_at": _epoch_ms_to_dt(msg.get("internalDate")).isoformat(),
        "body": body,
        "snippet_present": bool(body),
    }


def record_to_signal(record: Dict[str, Any], tenant_id: str,
                     source_system: SourceSystem = SourceSystem.GMAIL) -> SignalEvent:
    """Map a flattened Gmail metadata record → EMAIL SignalEvent (shared helper)."""
    return email_record_to_signal(record, tenant_id, source_system,
                                  source_endpoint="/gmail/push", extraction_prefix="gmail")
