"""Outlook connector — Graph webhook push + delta pull, metadata only.

  * **push** — ``POST /outlook/webhook`` receives Graph change notifications.
    :meth:`verify` handles the ``validationToken`` handshake and ``clientState``
    auth; :meth:`ingest` reads each notification's ``resourceData.id`` and (with a
    live client) fetches that message's *metadata* and emits an OUTLOOK event.
  * **pull** — :meth:`poll` runs a ``messages/delta`` query from the saved
    ``@odata.deltaLink`` watermark, so a restart resumes with no gaps/dupes.

In dev/replay mode (no client) the connector accepts inline message metadata, so
the fixture demo runs without Azure.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from connectors.common.msgraph_webhook import notification_items, verify_graph_webhook
from connectors.common.poller import PollingConnector
from core.connector import InboundRequest, VerifyResult
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .client import OutlookClient
from .mappers import message_to_signal, record_to_signal

logger = logging.getLogger("signalfabric.connector.outlook")


class OutlookConnector(PollingConnector):
    name = "outlook"
    source_system = SourceSystem.OUTLOOK

    def __init__(
        self,
        *,
        tenant_id: str,
        client: Optional[OutlookClient] = None,
        folder: str = "inbox",
        client_state: str = "",
        dev_allow_unverified: bool = True,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        super().__init__(tenant_id=tenant_id, start_cursor="", watermarks=watermarks)
        self.client = client
        self.folder = folder
        self._client_state = client_state
        self._dev_allow_unverified = dev_allow_unverified
        self._seen: set[str] = set()

    # ---- push: verify ----
    def verify(self, request: InboundRequest) -> VerifyResult:
        return verify_graph_webhook(request, client_state=self._client_state,
                                    dev_allow_unverified=self._dev_allow_unverified)

    # ---- ingest ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        # (a) flattened metadata record (fixture/replay)
        if "message_id" in raw and "from" in raw and "toRecipients" not in raw:
            return [record_to_signal(raw, self._tenant_id)]
        # (b) a single Graph message resource (fixture/replay)
        if "toRecipients" in raw or "internetMessageId" in raw:
            return [message_to_signal(raw, self._tenant_id)]
        # (c) a Graph change-notification body
        out: List[SignalEvent] = []
        for item in notification_items(raw):
            rd = item.get("resourceData") or {}
            mid = rd.get("id")
            if not mid or mid in self._seen:
                continue
            self._seen.add(mid)
            inline = item.get("_message")  # fixtures may inline the message
            if inline is not None:
                out.append(message_to_signal(inline, self._tenant_id))
            elif self.client is not None:
                out.append(message_to_signal(self.client.get_message(mid), self._tenant_id))
        return out

    # ---- pull (delta) ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        if self.client is None:
            return []
        items, delta_link = self.client.delta(self.folder, start=self._cursor or None)
        out: List[SignalEvent] = []
        for msg in items:
            if msg.get("@removed"):  # deletions — skip (no body, no edge to add)
                continue
            out.append(message_to_signal(msg, self._tenant_id))
            if limit and len(out) >= limit:
                break
        if delta_link:
            self.commit(delta_link)
        if out:
            logger.info("outlook poll: %d emails", len(out))
        return out
