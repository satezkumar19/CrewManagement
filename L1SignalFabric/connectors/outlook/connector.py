"""Outlook connector — Graph app-only unread-poll, metadata only.

Instead of a ``messages/delta`` watermark + webhook push, the connector **polls
the mailbox for unread messages** and **marks each as read** once emitted.
Marking-as-read is the dedupe/checkpoint mechanism — a restart simply re-lists
whatever is still unread, so there are no gaps or duplicates and no delta link to
persist.

  * **pull** — :meth:`poll` lists unread messages, emits one OUTLOOK event per
    message, then marks them read (when ``mark_read`` is on).
  * **push** — a Graph change notification on ``POST /outlook/webhook`` simply
    kicks a poll; :meth:`verify` still handles the ``validationToken`` handshake
    and ``clientState`` auth.

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

from .client import OutlookClient, OutlookClientError
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
        top: int = 50,
        mark_read: bool = True,
        client_state: str = "",
        dev_allow_unverified: bool = True,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        super().__init__(tenant_id=tenant_id, start_cursor="", watermarks=watermarks)
        self.client = client
        self.top = top
        self.mark_read = mark_read
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
        # (c) a Graph change-notification body. Replay fixtures may inline the
        #     message (`_message`); map those directly (dedup by resourceData id).
        #     A live notification (no inline) just signals "something changed", so
        #     kick an unread poll — that is the source of truth.
        out: List[SignalEvent] = []
        saw_notification = False
        for item in notification_items(raw):
            saw_notification = True
            inline = item.get("_message")
            if inline is None:
                continue
            rd = item.get("resourceData") or {}
            mid = rd.get("id") or inline.get("id")
            if mid and mid in self._seen:
                continue
            if mid:
                self._seen.add(mid)
            out.append(message_to_signal(inline, self._tenant_id))
        if out:
            return out
        if saw_notification and self.client is not None:
            return await self.poll()
        return []

    # ---- pull (unread poll + mark-read) ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        if self.client is None:
            return []
        try:
            messages = self.client.list_unread(top=self.top)
        except OutlookClientError as exc:
            logger.warning("outlook poll failed: %s", exc)
            return []
        out: List[SignalEvent] = []
        # list_unread is newest-first; process oldest-first so emission order
        # matches arrival order.
        for msg in reversed(messages):
            mid = msg.get("id")
            if not mid or mid in self._seen:
                continue
            self._seen.add(mid)
            out.append(message_to_signal(msg, self._tenant_id))
            if self.mark_read:
                try:
                    self.client.mark_read(mid)
                except OutlookClientError as exc:
                    logger.warning("outlook mark_read failed for %s: %s", mid, exc)
            if limit and len(out) >= limit:
                break
        if out:
            logger.info("outlook poll: %d emails", len(out))
        return out
