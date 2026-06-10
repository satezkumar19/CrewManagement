"""Gmail connector — Pub/Sub push + history pull, metadata only.

Two ingestion paths, one normalizer:

  * **push** — ``POST /gmail/push`` delivers a Pub/Sub envelope carrying only a
    ``historyId`` notification. :meth:`verify` authenticates it (shared-secret
    token or OIDC JWT); :meth:`ingest` decodes it and, with a live client,
    expands ``history.list`` from the stored watermark to fetch the *metadata* of
    each newly-added message, advancing the ``historyId`` watermark.
  * **pull** — :meth:`poll` walks ``history.list`` since the watermark (or, for a
    cold start, ``messages.list`` over a query window) and emits the same events.

In dev/replay mode (no client) the connector accepts pre-expanded message
metadata directly, so the fixture demo runs without GCP.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, List, Optional

from connectors.common.poller import PollingConnector
from core.connector import InboundRequest, VerifyResult
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .client import GmailClient
from .mappers import message_metadata_to_record, record_to_signal
from .verify import verify_oidc_jwt, verify_pubsub_token

logger = logging.getLogger("signalfabric.connector.gmail")


class GmailConnector(PollingConnector):
    name = "gmail"
    source_system = SourceSystem.GMAIL

    def __init__(
        self,
        *,
        tenant_id: str,
        client: Optional[GmailClient] = None,
        pubsub_token: str = "",
        oidc_audience: str = "",
        dev_allow_unverified: bool = True,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        super().__init__(tenant_id=tenant_id, start_cursor="", watermarks=watermarks)
        self.client = client
        self._pubsub_token = pubsub_token
        self._oidc_audience = oidc_audience
        self._dev_allow_unverified = dev_allow_unverified
        self._seen_pubsub_ids: set[str] = set()

    # ---- push: verify ----
    def verify(self, request: InboundRequest) -> VerifyResult:
        if self._pubsub_token:
            # Pub/Sub echoes the shared secret on the push URL query string
            # (?token=...); accept a header form too for proxied deployments.
            received = request.q("token") or request.header("x-pubsub-token")
            check = verify_pubsub_token(
                configured_token=self._pubsub_token,
                received_token=received,
            )
            return VerifyResult.ok() if check.ok else VerifyResult.reject(check.reason)
        if self._oidc_audience:
            check = verify_oidc_jwt(bearer=request.header("authorization"),
                                    audience=self._oidc_audience)
            return VerifyResult.ok() if check.ok else VerifyResult.reject(check.reason)
        if self._dev_allow_unverified:
            logger.warning("gmail push NOT verified (dev mode, no pubsub token)")
            return VerifyResult.ok()
        return VerifyResult.reject("no pubsub token / oidc audience configured")

    # ---- helpers ----
    @staticmethod
    def _decode_envelope(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
        message = raw.get("message")
        if not isinstance(message, dict):
            return None
        data_b64 = message.get("data")
        if not data_b64:
            return {"_messageId": message.get("messageId")}
        try:
            decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        except (ValueError, TypeError):
            return None
        decoded["_messageId"] = message.get("messageId")
        return decoded

    def _expand_history(self, new_history_id: str) -> List[SignalEvent]:
        """With a live client, fetch metadata for messages added since watermark.

        Cold start (no stored cursor) can't use ``new_history_id`` as the start:
        ``history.list`` returns only changes *after* that id, and the message
        that triggered this push sits *at* it — so the expansion would come back
        empty and the first e-mail after a (re)start would be silently dropped.
        Instead we backfill a recent window once to establish the baseline; from
        then on the persisted cursor drives normal incremental expansion.
        """
        if self.client is None:
            return []
        if not self._cursor:
            return self._cold_start_backfill()
        out: List[SignalEvent] = []
        seen_ids: set[str] = set()
        for record in self.client.history_list(self._cursor):
            for added in record.get("messagesAdded", []):
                mid = (added.get("message") or {}).get("id")
                if not mid or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                meta = self.client.get_message(mid)
                out.append(record_to_signal(message_metadata_to_record(meta),
                                            self._tenant_id, SourceSystem.GMAIL))
        if new_history_id:
            self.commit(str(new_history_id))
        return out

    def _cold_start_backfill(self, query: str = "newer_than:1d",
                             limit: Optional[int] = 50) -> List[SignalEvent]:
        """Enumerate a recent window once when no watermark exists, then commit the
        mailbox's current ``historyId`` as the baseline for incremental expansion.

        Bus dedup (``dedup_id`` per message) makes a re-enumerated message a no-op,
        so an overlap with a later push is harmless. The window is deliberately
        tight (last day) so a fresh start surfaces the just-arrived e-mail without
        replaying the whole mailbox."""
        if self.client is None:
            return []
        out: List[SignalEvent] = []
        seen_ids: set[str] = set()
        for ref in self.client.list_messages(query=query):
            mid = ref.get("id", "")
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            meta = self.client.get_message(mid)
            out.append(record_to_signal(message_metadata_to_record(meta),
                                        self._tenant_id, SourceSystem.GMAIL))
            if limit and len(out) >= limit:
                break
        hid = self.client.get_profile().get("historyId", "")
        if hid:
            self.commit(str(hid))
        logger.info("gmail cold-start backfill: %d message(s) over %s; baseline historyId=%s",
                    len(out), query, hid or "?")
        return out

    # ---- ingest (push) ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        # (a) already-flattened metadata record (fixture/replay)
        if "message_id" in raw and "payload" not in raw and "message" not in raw:
            return [record_to_signal(raw, self._tenant_id, SourceSystem.GMAIL)]
        # (b) a raw Gmail messages.get metadata payload (fixture/replay)
        if "payload" in raw:
            return [record_to_signal(message_metadata_to_record(raw),
                                     self._tenant_id, SourceSystem.GMAIL)]
        # (c) a Pub/Sub push envelope
        notif = self._decode_envelope(raw)
        if notif is None:
            return []
        pid = notif.get("_messageId")
        if pid and pid in self._seen_pubsub_ids:
            logger.debug("gmail duplicate pubsub message dropped: %s", pid)
            return []
        if pid:
            self._seen_pubsub_ids.add(pid)
        # fixtures may inline expanded messages so the demo works without a client
        inline = notif.get("_messages")
        if inline:
            return [record_to_signal(message_metadata_to_record(m) if "payload" in m else m,
                                     self._tenant_id, SourceSystem.GMAIL) for m in inline]
        return self._expand_history(str(notif.get("historyId", "")))

    # ---- pull ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        if self.client is None:
            return []
        if self._cursor:
            return self._expand_history(self.client.get_profile().get("historyId", ""))
        # cold start: enumerate a window (wider for an explicit pull), then advance
        # the watermark to the mailbox's current historyId.
        return self._cold_start_backfill(query="newer_than:7d", limit=limit)
