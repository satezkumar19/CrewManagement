"""Pure mappers: Microsoft Graph mail metadata → canonical OUTLOOK SignalEvent.

Flattens a Graph ``message`` resource into the shared e-mail metadata record and
delegates to :func:`connectors.common.email.email_record_to_signal`, so Outlook
and Gmail produce structurally-identical EMAIL events (and the same sign-off
detection). Body is never read.
"""

from __future__ import annotations

from typing import Any, Dict, List

from connectors.common.email import email_record_to_signal
from core.signal import SignalEvent, SourceSystem


def _addresses(recipients: List[Dict[str, Any]]) -> List[str]:
    out = []
    for r in recipients or []:
        addr = (r.get("emailAddress") or {}).get("address")
        if addr:
            out.append(addr)
    return out


def graph_message_to_record(msg: Dict[str, Any]) -> Dict[str, Any]:
    sender = (msg.get("from") or {}).get("emailAddress", {}) or {}
    return {
        "message_id": msg.get("internetMessageId") or msg.get("id", ""),
        "thread_id": msg.get("conversationId"),
        "from": sender.get("address"),
        "to": _addresses(msg.get("toRecipients", [])),
        "cc": _addresses(msg.get("ccRecipients", [])),
        "subject": msg.get("subject", ""),
        "labels": msg.get("categories", []),
        "sent_at": msg.get("receivedDateTime") or msg.get("sentDateTime"),
    }


def record_to_signal(record: Dict[str, Any], tenant_id: str) -> SignalEvent:
    return email_record_to_signal(record, tenant_id, SourceSystem.OUTLOOK,
                                  source_endpoint="/outlook/webhook",
                                  extraction_prefix="outlook")


def message_to_signal(msg: Dict[str, Any], tenant_id: str) -> SignalEvent:
    return record_to_signal(graph_message_to_record(msg), tenant_id)
