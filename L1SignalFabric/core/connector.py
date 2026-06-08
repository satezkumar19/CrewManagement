"""The connector contract — `EventStreamConnector`.

This is the L1 realization of Conduit's stubbed Phase-3 ``CDCExtractor``. Every
source (Slack, Gmail, ERP) implements the same lifecycle so the core treats them
uniformly, whether the source *pushes* (webhook) or is *pulled* (CDC/outbox):

    verify(request)  -> VerifyResult      # authenticate an inbound push (or a poll cycle)
    ingest(raw)      -> list[SignalEvent]  # normalize one raw payload into 0..N events
    position()       -> Checkpoint | None  # resume watermark (pull connectors)
    commit(ckpt)                            # persist position after successful sink

Push connectors (Slack, Gmail) override ``verify`` + ``ingest``; the route calls
them. Pull connectors (ERP) additionally use ``position``/``commit`` and a
``poll`` helper. Defaults are provided so each connector only implements what it
actually needs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .signal import SignalEvent, SourceSystem

# A checkpoint is whatever a source uses to mark progress: an int seq for an
# outbox/CDC feed, an ISO timestamp for a manifest, a Gmail historyId, etc.
Checkpoint = Any


@dataclass
class InboundRequest:
    """Framework-agnostic view of an inbound HTTP push.

    The FastAPI route adapts ``starlette.Request`` into this so connectors never
    depend on the web framework (and are trivially unit-testable).
    """

    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    json: Optional[dict[str, Any]] = None
    query: dict[str, str] = field(default_factory=dict)

    def header(self, name: str, default: str = "") -> str:
        # HTTP headers are case-insensitive.
        lname = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lname:
                return v
        return default

    def q(self, name: str, default: str = "") -> str:
        """Read a query-string parameter (used by webhook handshakes such as
        Microsoft Graph's ``validationToken`` and Gmail Pub/Sub's ``token``)."""
        return self.query.get(name, default)


class VerifyOutcome(str, Enum):
    OK = "ok"              # authentic — proceed to ingest
    CHALLENGE = "challenge"  # handshake — echo `challenge` back, do not ingest
    REJECT = "reject"     # inauthentic — return 401, do not ingest


@dataclass
class VerifyResult:
    outcome: VerifyOutcome
    challenge: Optional[str] = None   # set when outcome == CHALLENGE
    reason: Optional[str] = None      # set when outcome == REJECT (for logging)

    @classmethod
    def ok(cls) -> "VerifyResult":
        return cls(VerifyOutcome.OK)

    @classmethod
    def challenge_with(cls, value: str) -> "VerifyResult":
        return cls(VerifyOutcome.CHALLENGE, challenge=value)

    @classmethod
    def reject(cls, reason: str) -> "VerifyResult":
        return cls(VerifyOutcome.REJECT, reason=reason)


class EventStreamConnector(ABC):
    """Base class for all L1 source connectors."""

    #: stable connector id, e.g. "slack" | "gmail" | "erp"
    name: str = "connector"
    #: the (representative) source system this connector emits
    source_system: SourceSystem

    # --- push path -------------------------------------------------------
    def verify(self, request: InboundRequest) -> VerifyResult:
        """Authenticate an inbound push. Default: accept (pull connectors that
        are never called over HTTP rely on this no-op)."""
        return VerifyResult.ok()

    @abstractmethod
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one raw payload (a webhook body, a poll record) into 0..N
        canonical :class:`SignalEvent`. Pure-ish: no I/O beyond reading config."""

    # --- pull path (override in CDC/outbox connectors) -------------------
    def position(self) -> Optional[Checkpoint]:
        """Current resume watermark, or None for push-only connectors."""
        return None

    def commit(self, checkpoint: Checkpoint) -> None:
        """Persist the watermark after the sink has acked. No-op by default."""
        return None
