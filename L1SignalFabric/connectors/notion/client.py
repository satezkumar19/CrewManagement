"""Notion API client — the real pages/databases/blocks client.

Ports the upstream Notion scraper's ``client.py`` onto the shared
:class:`~connectors.common.http.RateLimitedClient`, talking directly to the
Notion HTTPS API (no ``notion-client`` dependency). Same endpoints, same 350 ms
pacing (~3 req/s, Notion's limit), same fail-after-3-consecutive-429s guard,
same cursor pagination (page_size 100).

Endpoints covered: search · pages.retrieve · databases.retrieve ·
databases.query · blocks.children.list · users.retrieve
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from connectors.common import RateLimitedClient, StructuredLogger

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
PAGE_SIZE = 100


class NotionClient:
    def __init__(
        self,
        token: str,
        logger: Optional[StructuredLogger] = None,
        *,
        rate_limit_delay_ms: int = 350,
        max_rate_limit_errors: int = 3,
        notion_version: str = NOTION_VERSION,
    ) -> None:
        self.logger = logger or StructuredLogger(console_output=False)
        self._http = RateLimitedClient(
            base_url=NOTION_API_BASE,
            logger=self.logger,
            default_headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": notion_version,
                "Content-Type": "application/json",
            },
            rate_limit_delay_ms=rate_limit_delay_ms,
            max_rate_limit_errors=max_rate_limit_errors,
        )

    @property
    def api_calls(self) -> int:
        return self._http.api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._http.rate_limit_hits

    # --- diagnostics ---
    def get_self(self) -> Dict[str, Any]:
        d = self._http.post("search", json={"page_size": 1})
        return {"has_access": True, "results_count": len(d.get("results", []))}

    # --- search ---
    def search(self, query: str = "", filter_type: Optional[str] = None,
               start_cursor: Optional[str] = None, page_size: int = PAGE_SIZE) -> Dict[str, Any]:
        body: Dict[str, Any] = {"page_size": min(page_size, PAGE_SIZE)}
        if query:
            body["query"] = query
        if filter_type in ("page", "database"):
            body["filter"] = {"property": "object", "value": filter_type}
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._http.post("search", json=body)

    def search_all(self, query: str = "",
                   filter_type: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            page = self.search(query=query, filter_type=filter_type, start_cursor=cursor)
            yield from page.get("results", [])
            if not page.get("has_more"):
                return
            cursor = page.get("next_cursor")
            if not cursor:
                return

    # --- pages / databases ---
    def get_page(self, page_id: str) -> Dict[str, Any]:
        return self._http.get(f"pages/{page_id}")

    def get_database(self, database_id: str) -> Dict[str, Any]:
        return self._http.get(f"databases/{database_id}")

    def query_database(self, database_id: str, start_cursor: Optional[str] = None,
                       page_size: int = PAGE_SIZE) -> Dict[str, Any]:
        body: Dict[str, Any] = {"page_size": min(page_size, PAGE_SIZE)}
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._http.post(f"databases/{database_id}/query", json=body)

    def query_database_all(self, database_id: str) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            page = self.query_database(database_id, start_cursor=cursor)
            yield from page.get("results", [])
            if not page.get("has_more"):
                return
            cursor = page.get("next_cursor")
            if not cursor:
                return

    # --- blocks ---
    def get_block_children(self, block_id: str, start_cursor: Optional[str] = None,
                           page_size: int = PAGE_SIZE) -> Dict[str, Any]:
        params: Dict[str, Any] = {"page_size": min(page_size, PAGE_SIZE)}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._http.get(f"blocks/{block_id}/children", params=params)

    def get_all_blocks(self, block_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            page = self.get_block_children(block_id, start_cursor=cursor)
            out.extend(page.get("results", []))
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return out

    # --- users ---
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._http.get(f"users/{user_id}")
        except Exception as exc:  # noqa: BLE001
            self.logger.warn("users.retrieve failed", user=user_id, error=str(exc))
            return None
