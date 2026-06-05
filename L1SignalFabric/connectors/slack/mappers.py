"""Pure mappers: raw Slack event payloads -> canonical SignalEvent(s).

These are the Slack half of the "Normalizer" seam (raw -> SignalEvent). Keeping
them pure (no I/O, no clock beyond the supplied timestamps) means they are proven
against recorded fixtures and reused unchanged whether the event arrives via the
HTTP Events API or Socket Mode.

Handled event types (Day-1 skeleton scope): message, reaction_added,
member_joined_channel. Each feeds OrgMap *tribal knowledge* only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.signal import Lineage, SignalEvent, SourceSystem

# Slack `ts` is a string like "1719980964.000100" (epoch seconds.fraction).


def _ts_to_dt(ts: Optional[str]) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _lineage(envelope: dict[str, Any]) -> Lineage:
    return Lineage(
        extraction_id=f"slack-{envelope.get('event_id', 'unknown')}",
        source_endpoint="/slack/events",
    )


def map_message(event: dict[str, Any], envelope: dict[str, Any], tenant_id: str) -> SignalEvent:
    channel = event.get("channel", "")
    ts = event.get("ts", "")
    return SignalEvent(
        entity="message",
        key={"channel_id": channel, "ts": ts},
        source_system=SourceSystem.SLACK,
        tenant_id=tenant_id,
        data={
            "channel": channel,
            "user": event.get("user", ""),
            "text": event.get("text", ""),
            "thread_ts": event.get("thread_ts"),
            "team": envelope.get("team_id"),
        },
        timestamp=_ts_to_dt(ts),
        lineage=_lineage(envelope),
        metadata={"eventId": envelope.get("event_id"), "schemaVersion": "1.0"},
    )


def map_reaction(event: dict[str, Any], envelope: dict[str, Any], tenant_id: str) -> SignalEvent:
    item = event.get("item", {}) or {}
    channel = item.get("channel", "")
    target_ts = item.get("ts", "")
    return SignalEvent(
        entity="reaction",
        key={"channel_id": channel, "ts": target_ts, "reaction": event.get("reaction", "")},
        source_system=SourceSystem.SLACK,
        tenant_id=tenant_id,
        data={
            "channel": channel,
            "user": event.get("user", ""),
            "target_ts": target_ts,
            "reaction": event.get("reaction", ""),
        },
        timestamp=_ts_to_dt(event.get("event_ts")),
        lineage=_lineage(envelope),
        metadata={"eventId": envelope.get("event_id"), "schemaVersion": "1.0"},
    )


def map_member_joined(event: dict[str, Any], envelope: dict[str, Any], tenant_id: str) -> SignalEvent:
    channel = event.get("channel", "")
    user = event.get("user", "")
    return SignalEvent(
        entity="channel_join",
        key={"channel_id": channel, "user": user},
        source_system=SourceSystem.SLACK,
        tenant_id=tenant_id,
        data={
            "channel": channel,
            "user": user,
            "inviter": event.get("inviter"),
        },
        timestamp=_ts_to_dt(event.get("event_ts")),
        lineage=_lineage(envelope),
        metadata={"eventId": envelope.get("event_id"), "schemaVersion": "1.0"},
    )


# event.type -> mapper. Unhandled types are ignored (return []).
MAPPERS = {
    "message": map_message,
    "reaction_added": map_reaction,
    "member_joined_channel": map_member_joined,
}
