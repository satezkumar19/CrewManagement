"""Idempotency helper for the signal stream.

Connectors re-deliver the same source event constantly (Slack webhook retries,
Pub/Sub redelivery, polling overlaps). ``dedup_key`` is the natural identity of
an event so the bus / sink can drop duplicates: the same source event ingested
twice yields exactly one stored event.

(:pyattr:`core.signal.SignalEvent.dedup_id` is the canonical-event variant of
this; this free function is for hashing raw payloads before a SignalEvent
exists.)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional


def dedup_key(
    *,
    source: str,
    entity: str,
    occurred_at: str,
    payload: dict[str, Any],
    natural_keys: Optional[Iterable[str]] = None,
) -> str:
    """Stable, collision-resistant identity for a raw event.

    Args:
        source: SourceSystem value.
        entity: entity/event-type name.
        occurred_at: ISO-8601 timestamp of the source event.
        payload: the raw payload.
        natural_keys: payload keys that uniquely identify the event; if omitted
            the whole payload is hashed (order-independent).
    """
    identity: Any = (
        {k: payload.get(k) for k in natural_keys}
        if natural_keys is not None
        else payload
    )
    blob = json.dumps(
        {"source": source, "entity": entity, "occurred_at": occurred_at, "identity": identity},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
