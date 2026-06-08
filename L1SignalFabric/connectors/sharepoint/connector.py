"""SharePoint connector — Graph delta pull + webhook push.

Configured with one or more *targets* — document-library drives and/or
SharePoint lists. Each target keeps its own ``@odata.deltaLink`` watermark, so
:meth:`poll` only emits items changed since the last run (incremental, resumable
across restarts). A Graph webhook (``POST /sharepoint/webhook``) notifies that a
target changed; :meth:`verify` handles the handshake/clientState and the route
re-polls the affected target.

Because each target has an independent watermark, this connector manages a
:class:`~core.watermark.WatermarkStore` directly rather than the single-cursor
base; :meth:`position` / :meth:`commit` expose the full per-target snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from connectors.common.msgraph_webhook import verify_graph_webhook
from core.connector import Checkpoint, EventStreamConnector, InboundRequest, VerifyResult
from core.signal import SignalEvent, SourceSystem
from core.watermark import InMemoryWatermarkStore, WatermarkStore

from .client import SharePointClient
from .mappers import drive_item_to_signal, list_item_to_signal

logger = logging.getLogger("signalfabric.connector.sharepoint")


@dataclass
class DriveTarget:
    drive_id: str

    @property
    def wm_key(self) -> str:
        return f"sharepoint:drive:{self.drive_id}"


@dataclass
class ListTarget:
    site_id: str
    list_id: str

    @property
    def wm_key(self) -> str:
        return f"sharepoint:list:{self.site_id}:{self.list_id}"


class SharePointConnector(EventStreamConnector):
    name = "sharepoint"
    source_system = SourceSystem.SHAREPOINT

    def __init__(
        self,
        *,
        tenant_id: str,
        client: Optional[SharePointClient] = None,
        targets: Optional[List[Any]] = None,
        client_state: str = "",
        dev_allow_unverified: bool = True,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        self._tenant_id = tenant_id
        self.client = client
        self.targets: List[Any] = targets or []
        self._client_state = client_state
        self._dev_allow_unverified = dev_allow_unverified
        self._wm = watermarks or InMemoryWatermarkStore()

    # ---- push: verify ----
    def verify(self, request: InboundRequest) -> VerifyResult:
        return verify_graph_webhook(request, client_state=self._client_state,
                                    dev_allow_unverified=self._dev_allow_unverified)

    # ---- watermark snapshot (per target) ----
    def position(self) -> Checkpoint:
        return {t.wm_key: self._wm.get(t.wm_key, "") for t in self.targets}

    def commit(self, checkpoint: Checkpoint) -> None:
        for key, link in (checkpoint or {}).items():
            self._wm.set(key, link)

    # ---- ingest (fixture/replay + webhook) ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        # a single list item (has fields) or drive item (has name/file/folder)
        if "fields" in raw and "name" not in raw:
            return [list_item_to_signal(raw, self._tenant_id)]
        if "name" in raw or "file" in raw or "folder" in raw:
            return [drive_item_to_signal(raw, self._tenant_id)]
        # a Graph webhook body → trigger a delta poll of all targets
        if "value" in raw and self.client is not None:
            return await self.poll()
        return []

    # ---- pull (delta over every target) ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        if self.client is None:
            return []
        out: List[SignalEvent] = []
        new_links: Dict[str, str] = {}
        for target in self.targets:
            start = self._wm.get(target.wm_key, "") or None
            try:
                if isinstance(target, DriveTarget):
                    items, link = self.client.drive_delta(target.drive_id, start=start)
                    for it in items:
                        if "root" in it:  # the drive-root anchor (facet may be {}) — not a real item
                            continue
                        out.append(drive_item_to_signal(it, self._tenant_id, target.drive_id))
                else:
                    items, link = self.client.list_delta(target.site_id, target.list_id, start=start)
                    for it in items:
                        out.append(list_item_to_signal(it, self._tenant_id,
                                                       target.site_id, target.list_id))
                if link:
                    new_links[target.wm_key] = link
            except Exception as exc:  # noqa: BLE001 - one bad target must not abort others
                logger.warning("sharepoint target poll failed (%s): %s", target.wm_key, exc)
            if limit and len(out) >= limit:
                break
        if new_links:
            self.commit(new_links)
        if out:
            logger.info("sharepoint poll: %d items across %d targets",
                        len(out), len(self.targets))
        return out
