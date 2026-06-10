"""SharePoint connector — Graph app-only folder listing, metadata only.

Instead of a per-drive/list ``delta`` watermark + webhook push, the connector
**lists the configured folder(s)** under one site's default document library and
emits a ``drive_item`` event per file/folder. Re-listing is idempotent — an
in-process ``seen`` set suppresses items already emitted this run, and a restart
simply re-lists the current folder contents.

  * **pull** — :meth:`poll` lists each configured folder and emits new items.
  * **push** — a Graph change notification on ``POST /sharepoint/webhook`` kicks
    a poll; :meth:`verify` handles the ``validationToken`` handshake + clientState.

In dev/replay mode (no client) the connector accepts an inline normalised folder
item, so the fixture demo runs without Azure.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from connectors.common.msgraph_webhook import verify_graph_webhook
from connectors.common.poller import PollingConnector
from core.connector import InboundRequest, VerifyResult
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .client import SharePointClient, SharePointClientError
from .mappers import folder_item_to_signal

logger = logging.getLogger("signalfabric.connector.sharepoint")


class SharePointConnector(PollingConnector):
    name = "sharepoint"
    source_system = SourceSystem.SHAREPOINT

    def __init__(
        self,
        *,
        tenant_id: str,
        client: Optional[SharePointClient] = None,
        folder_paths: Optional[List[str]] = None,
        client_state: str = "",
        dev_allow_unverified: bool = True,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        super().__init__(tenant_id=tenant_id, start_cursor="", watermarks=watermarks)
        self.client = client
        self.folder_paths: List[str] = [f for f in (folder_paths or []) if f]
        self._client_state = client_state
        self._dev_allow_unverified = dev_allow_unverified
        self._seen: set[str] = set()

    # ---- push: verify ----
    def verify(self, request: InboundRequest) -> VerifyResult:
        return verify_graph_webhook(request, client_state=self._client_state,
                                    dev_allow_unverified=self._dev_allow_unverified)

    # ---- ingest (fixture/replay + webhook) ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        # a single normalised folder item (fixture/replay)
        if "id" in raw and "name" in raw and ("is_folder" in raw or "web_url" in raw):
            return [self._to_signal(raw)]
        # a Graph webhook body → trigger a folder poll
        if "value" in raw and self.client is not None:
            return await self.poll()
        return []

    def _to_signal(self, item: dict[str, Any], folder_path: str = "") -> SignalEvent:
        hostname = getattr(self.client, "hostname", "") if self.client else ""
        site_path = getattr(self.client, "site_path", "") if self.client else ""
        return folder_item_to_signal(item, self._tenant_id, hostname=hostname,
                                     site_path=site_path, folder_path=folder_path)

    # ---- pull (list configured folders) ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        if self.client is None:
            return []
        out: List[SignalEvent] = []
        for folder in self.folder_paths:
            try:
                items = self.client.list_folder(folder)
            except SharePointClientError as exc:  # one bad folder must not abort others
                logger.warning("sharepoint folder poll failed (%s): %s", folder, exc)
                continue
            for it in items:
                iid = it.get("id")
                if not iid or iid in self._seen:
                    continue
                self._seen.add(iid)
                out.append(self._to_signal(it, folder_path=folder))
                if limit and len(out) >= limit:
                    return out
        if out:
            logger.info("sharepoint poll: %d items across %d folder(s)",
                        len(out), len(self.folder_paths))
        return out
