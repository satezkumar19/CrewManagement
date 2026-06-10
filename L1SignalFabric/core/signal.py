"""Canonical event model for L1 SignalFabric.

`SignalEvent` is the one shape every connector emits and the event bus / L2 sink
consume. It is 1:1 with the upstream `Record` (entity, key, sourceSystem,
tenantId, data, operation, timestamp, lineage) so the stream stays
batch-compatible — the only difference from the upstream batch file path is that
L1 is continuous, so `operation` is always ``DELTA`` (never ``SNAPSHOT``).

This module is the **jointly-agreed Day-1 contract** (see PLAN §3 "Shared/agreed
Day 1"): both the ingress/connector track and the bus/sink track build against it.
It is intentionally dependency-light (pydantic only) so it can later be vendored
back upstream unchanged.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceSystem(str, Enum):
    """Source systems L1 SignalFabric ingests from.

    SLACK / EMAIL already exist in the upstream enum; the ERP members
    (Crew DB, Contract/CLM, Vessel/Port DB) and the real-connector members
    (Notion, Gmail, Outlook, SharePoint, generic Database) are the L1 extension.

    ``GMAIL`` / ``OUTLOOK`` are concrete e-mail providers; both belong to the
    "e-mail family" (see :data:`EMAIL_FAMILY`) that the L2 sink treats uniformly
    for OrgMap edges and SignOffEvent materialization. ``EMAIL`` is retained for
    the provider-agnostic demo normalizer and for upstream compatibility.
    """

    SLACK = "SLACK"                      # Slack Events API + Web API backfill
    EMAIL = "EMAIL"                      # provider-agnostic e-mail metadata (demo)
    GMAIL = "GMAIL"                      # Gmail API (Pub/Sub push + history backfill)
    OUTLOOK = "OUTLOOK"                  # Microsoft Graph mail (app-only unread poll)
    NOTION = "NOTION"                    # Notion API (pages / databases / blocks)
    SHAREPOINT = "SHAREPOINT"            # Microsoft Graph (app-only folder listing)
    DATABASE = "DATABASE"                # generic SQL CDC / outbox feed
    CREW_DB = "CREW_DB"                  # ERP — crew master
    CONTRACT_CLM = "CONTRACT_CLM"        # ERP — contract lifecycle mgmt
    VESSEL_PORT_DB = "VESSEL_PORT_DB"    # ERP — vessel / port reference


#: Source systems whose events the L2 sink projects as e-mail OrgMap edges and
#: that can carry an ``l2Intent = CREATE_SIGNOFF_EVENT`` sign-off marker.
EMAIL_FAMILY = frozenset({SourceSystem.EMAIL, SourceSystem.GMAIL, SourceSystem.OUTLOOK})


class Operation(str, Enum):
    """How downstream should interpret the record. L1 streams are always DELTA;
    SNAPSHOT exists only for parity with the upstream batch file path."""

    DELTA = "DELTA"
    SNAPSHOT = "SNAPSHOT"


class Lineage(BaseModel):
    """Provenance for one event — where it came from, for debugging and audit."""

    extraction_id: str
    source_endpoint: Optional[str] = None   # "/slack/events", "/gmail/push", "erp.outbox"
    source_sequence: Optional[int] = None   # outbox id / gmail historyId
    checksum: Optional[str] = None          # sha256 of the raw payload


class SignalEvent(BaseModel):
    """An immutable, normalized event in the SignalFabric stream."""

    entity: str                              # message | reaction | channel_join | email | crew | ...
    key: dict[str, Any]                      # source-natural primary key
    source_system: SourceSystem
    tenant_id: str
    operation: Operation = Operation.DELTA
    data: dict[str, Any] = Field(default_factory=dict)

    timestamp: datetime                      # when the event was valid at source
    extracted_at: datetime = Field(default_factory=utcnow)  # when L1 received it (latency start)

    lineage: Optional[Lineage] = None
    metadata: dict[str, Any] = Field(default_factory=dict)   # schemaVersion, eventId, l2Intent, ...

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("timestamp", "extracted_at")
    @classmethod
    def _require_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (use UTC)")
        return v.astimezone(timezone.utc)

    @property
    def dedup_id(self) -> str:
        """Stable identity for at-least-once delivery: same source event ingested
        twice yields the same id, so the bus / sink can drop duplicates.

        Identity = (source_system, entity, key, source_sequence?). The native
        source id (Slack event_id, Pub/Sub messageId) is folded in via key/seq.
        """
        seq = self.lineage.source_sequence if self.lineage else None
        blob = "|".join(
            [
                self.source_system.value,
                self.entity,
                repr(sorted(self.key.items())),
                str(seq),
            ]
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
