"""Notion connector — watermark-checkpointed pull over pages & databases.

Realizes the upstream Notion *scraper* as an L1 pull connector. It discovers all
accessible pages and databases via ``search``, extracts each page's flattened
block content (and each database row's properties), and emits canonical
SignalEvents. The watermark is the newest ``last_edited_time`` emitted, so:

  * ``poll()`` only re-emits pages edited since the last run (incremental sync,
    the scraper's ``--since`` made automatic & crash-safe), and
  * ``scrape()`` does a full pass to a batch-compatible JSONL bundle and/or the
    bus.

Page-object construction, title extraction, user extraction and per-run metrics
are preserved from the scraper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from connectors.common import (
    OutputWriter,
    RateLimitError,
    ScrapeMetrics,
    StructuredLogger,
)
from connectors.common.poller import PollingConnector
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .block_parser import (
    BlockParser,
    extract_properties_as_text,
    extract_simplified_properties,
)
from .client import NotionClient
from .mappers import page_to_signal
from .models import NotionPage, NotionUser

EmitFn = Callable[[SignalEvent], Union[None, Awaitable[None]]]
_GENESIS = "1970-01-01T00:00:00+00:00"


def _parse_ts(ts: Optional[str]) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


class NotionConnector(PollingConnector):
    name = "notion"
    source_system = SourceSystem.NOTION

    def __init__(
        self,
        *,
        tenant_id: str,
        client: NotionClient,
        logger: Optional[StructuredLogger] = None,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        super().__init__(tenant_id=tenant_id, start_cursor=_GENESIS, watermarks=watermarks)
        self.client = client
        self.logger = logger or StructuredLogger(console_output=False)
        self.block_parser = BlockParser(client, self.logger)

    # ---- page construction (ported from the scraper) ----
    def _extract_user(self, user_data: Dict[str, Any]) -> NotionUser:
        person = user_data.get("person", {}) if user_data.get("type") == "person" else {}
        return NotionUser(
            id=user_data.get("id", ""),
            name=user_data.get("name"),
            email=person.get("email"),
        )

    def _extract_title(self, data: Dict[str, Any]) -> str:
        props = data.get("properties", {}) or {}
        for prop in props.values():
            if prop.get("type") == "title":
                title_arr = prop.get("title", [])
                if title_arr:
                    return "".join(t.get("plain_text", "") for t in title_arr) or "Untitled"
        title_arr = data.get("title", [])
        if isinstance(title_arr, list) and title_arr:
            return "".join(t.get("plain_text", "") for t in title_arr) or "Untitled"
        return "Untitled"

    def _build_page(self, data: Dict[str, Any], content: str, *,
                    is_database_item: bool = False, database_id: Optional[str] = None,
                    properties: Optional[Dict[str, Any]] = None) -> NotionPage:
        parent = data.get("parent", {}) or {}
        parent_type = parent.get("type", "workspace")
        parent_id = parent.get(parent_type, "") if parent_type != "workspace" else "workspace"
        return NotionPage(
            page_id=data.get("id", ""),
            object_type="database_item" if is_database_item else data.get("object", "page"),
            title=self._extract_title(data),
            url=data.get("url", ""),
            parent_type=parent_type,
            parent_id=str(parent_id),
            created_time=_parse_ts(data.get("created_time")),
            last_edited_time=_parse_ts(data.get("last_edited_time")),
            created_by=self._extract_user(data.get("created_by", {}) or {}),
            last_edited_by=self._extract_user(data.get("last_edited_by", {}) or {}),
            content=content,
            properties=properties,
            is_database_item=is_database_item,
            database_id=database_id,
        )

    def _page_signal(self, data: Dict[str, Any]) -> SignalEvent:
        content = self.block_parser.extract_page_content(data.get("id", ""))
        return page_to_signal(self._build_page(data, content), self._tenant_id)

    def _db_item_signal(self, item: Dict[str, Any], database_id: str) -> SignalEvent:
        props = item.get("properties", {}) or {}
        prop_text = extract_properties_as_text(props)
        block_text = self.block_parser.extract_page_content(item.get("id", ""))
        content = "\n\n".join(p for p in (prop_text, block_text) if p)
        page = self._build_page(item, content, is_database_item=True,
                                database_id=database_id,
                                properties=extract_simplified_properties(props))
        return page_to_signal(page, self._tenant_id)

    # ---- L1 ingest contract ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one Notion object (page or database row).

        ``raw`` may carry ``_database_id`` to mark a database row.
        """
        db_id = raw.get("_database_id")
        if db_id:
            return [self._db_item_signal(raw, db_id)]
        if raw.get("object") == "database":
            return []  # databases themselves are containers; rows come via query
        return [self._page_signal(raw)]

    # ---- L1 poll contract (incremental via last_edited_time) ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        since = _parse_ts(self._cursor)
        out: List[SignalEvent] = []
        newest = since
        for obj in self.client.search_all():
            edited = _parse_ts(obj.get("last_edited_time"))
            if edited <= since:
                continue
            try:
                if obj.get("object") == "database":
                    for item in self.client.query_database_all(obj.get("id", "")):
                        out.append(self._db_item_signal(item, obj.get("id", "")))
                        newest = max(newest, _parse_ts(item.get("last_edited_time")))
                else:
                    out.append(self._page_signal(obj))
                newest = max(newest, edited)
            except RateLimitError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warn("poll item failed", id=obj.get("id"), error=str(exc))
            if limit and len(out) >= limit:
                break
        if newest > since:
            self.commit(newest.isoformat())
        if out:
            self.logger.info("notion poll", emitted=len(out), cursor=self._cursor)
        return out

    # ---- full scrape (CLI / batch-compatible bundle) ----
    def scrape(self, writer: Optional[OutputWriter] = None,
               on_event: Optional[EmitFn] = None,
               since: Optional[datetime] = None) -> ScrapeMetrics:
        metrics = ScrapeMetrics()
        metrics.extra.update({"databases": {"total": 0, "items_total": 0}, "blocks_fetched": 0})
        if writer is not None:
            writer.open()

        def _emit(signal: SignalEvent) -> None:
            metrics.records_total += 1
            metrics.records_successful += 1
            metrics.signals_emitted += 1
            if writer is not None:
                writer.write_event(signal)
            if on_event is not None:
                res = on_event(signal)
                if hasattr(res, "__await__"):
                    import asyncio
                    asyncio.get_event_loop().run_until_complete(res)

        try:
            for obj in self.client.search_all():
                if since and _parse_ts(obj.get("last_edited_time")) < since:
                    continue
                try:
                    if obj.get("object") == "database":
                        metrics.extra["databases"]["total"] += 1
                        for item in self.client.query_database_all(obj.get("id", "")):
                            metrics.extra["databases"]["items_total"] += 1
                            _emit(self._db_item_signal(item, obj.get("id", "")))
                    else:
                        _emit(self._page_signal(obj))
                except RateLimitError as exc:
                    metrics.add_error(f"rate limit: {exc}")
                    self.logger.error("aborting on rate limit", error=str(exc))
                    break
                except Exception as exc:  # noqa: BLE001
                    metrics.records_failed += 1
                    metrics.add_error(f"{obj.get('id')}: {exc}")
        finally:
            if writer is not None:
                writer.close()

        metrics.api_calls_total = self.client.api_calls
        metrics.api_rate_limit_hits = self.client.rate_limit_hits
        metrics.extra["blocks_fetched"] = self.block_parser.blocks_fetched
        metrics.finalize()
        if writer is not None:
            writer.write_manifest(SourceSystem.NOTION.value, metrics.records_total)
            writer.write_metrics(metrics)
        return metrics
