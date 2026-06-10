"""Rate-limited, retrying HTTP client — the shared spine of every real connector.

This generalises the ``_delay`` / ``_handle_rate_limit`` / ``_call_api`` triad
that the upstream Slack and Notion scrapers each hand-rolled into one reusable
client, so Slack, Notion, Gmail, Outlook and SharePoint all get identical,
battle-tested throttling and retry behaviour:

  * **fixed inter-call delay** (``rate_limit_delay_ms``) — the politeness pacing
    both scrapers used (Slack 1200 ms ≈ 50 req/min; Notion 350 ms ≈ 3 req/s).
  * **429 handling** — honour the ``Retry-After`` header, sleep, and retry; after
    ``max_rate_limit_errors`` *consecutive* 429s, raise :class:`RateLimitError`
    (the scrapers' fail-fast guard). The consecutive counter resets on success.
  * **5xx backoff** — bounded exponential backoff on transient server errors.
  * **metrics** — ``api_calls`` / ``rate_limit_hits`` counters, surfaced in the
    per-run metrics file exactly like the scrapers.

``requests`` is imported lazily so the package imports without it; a connector
only needs it when it actually talks to a live API.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, Optional

from .logger import StructuredLogger


class RateLimitError(Exception):
    """Raised when consecutive rate-limit responses exceed the configured max."""


class HTTPError(Exception):
    """Non-retryable HTTP error (4xx other than 429)."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


class RateLimitedClient:
    """A thin ``requests.Session`` wrapper with pacing, retry and metrics."""

    def __init__(
        self,
        *,
        base_url: str = "",
        logger: Optional[StructuredLogger] = None,
        default_headers: Optional[Dict[str, str]] = None,
        auth_provider: Optional[Callable[[], str]] = None,
        rate_limit_delay_ms: int = 350,
        max_rate_limit_errors: int = 3,
        max_server_retries: int = 3,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger or StructuredLogger(console_output=False)
        self.default_headers = default_headers or {}
        # Optional callable returning a fresh bearer token per request; lets a
        # connector carry a self-refreshing OAuth credential (e.g. Gmail's
        # refresh-token flow) instead of a frozen Authorization header.
        self.auth_provider = auth_provider
        self.rate_limit_delay_ms = rate_limit_delay_ms
        self.max_rate_limit_errors = max_rate_limit_errors
        self.max_server_retries = max_server_retries
        self.timeout = timeout
        self._sleep = sleep
        # metrics (mirrors the scrapers' SlackClient/NotionClient counters)
        self.api_calls = 0
        self.rate_limit_hits = 0
        self._consecutive_rate_limits = 0
        self._session = None  # lazy

    # ---- session / pacing -------------------------------------------------
    def _ensure_session(self):
        if self._session is None:
            import requests  # lazy: only needed for live calls
            self._session = requests.Session()
        return self._session

    def _delay(self) -> None:
        if self.rate_limit_delay_ms > 0:
            self._sleep(self.rate_limit_delay_ms / 1000.0)

    def _handle_rate_limit(self, retry_after: float) -> None:
        self.rate_limit_hits += 1
        self._consecutive_rate_limits += 1
        self.logger.warn("rate limited", retry_after=retry_after,
                          consecutive=self._consecutive_rate_limits)
        if self._consecutive_rate_limits >= self.max_rate_limit_errors:
            raise RateLimitError(
                f"exceeded {self.max_rate_limit_errors} consecutive rate limits"
            )
        self._sleep(retry_after)

    # ---- core request -----------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform one request with pacing + retry; return parsed JSON.

        Recurses on 429 (after sleeping ``Retry-After``) and retries 5xx with
        bounded exponential backoff. Raises :class:`HTTPError` on other 4xx and
        :class:`RateLimitError` when the consecutive-429 cap is hit.
        """
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        merged = {**self.default_headers, **(headers or {})}
        if self.auth_provider is not None:
            # Re-mint the bearer on every call so a refreshed token is picked up
            # transparently; an explicit per-call header still wins.
            merged.setdefault("Authorization", f"Bearer {self.auth_provider()}")
        session = self._ensure_session()

        server_attempts = 0
        while True:
            self._delay()
            self.api_calls += 1
            resp = session.request(method, url, params=params, json=json,
                                   data=data, headers=merged, timeout=self.timeout)
            status = resp.status_code

            if status == 429:
                retry_after = float(resp.headers.get("Retry-After", 30))
                self._handle_rate_limit(retry_after)
                continue  # retry same call
            if 500 <= status < 600:
                server_attempts += 1
                if server_attempts > self.max_server_retries:
                    raise HTTPError(status, "server error after retries", resp.text)
                backoff = min(2.0 ** server_attempts, 30.0)
                self.logger.warn("server error, backing off", status=status,
                                 attempt=server_attempts, backoff=backoff)
                self._sleep(backoff)
                continue
            if status >= 400:
                raise HTTPError(status, resp.reason or "client error",
                                _safe_json(resp))

            # success — reset the consecutive-429 guard
            self._consecutive_rate_limits = 0
            return _safe_json(resp) or {}

    # ---- convenience ------------------------------------------------------
    def get(self, path: str, **kw: Any) -> Dict[str, Any]:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Dict[str, Any]:
        return self.request("POST", path, **kw)

    def paginate(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        items_key: str,
        next_param: str,
        next_from: Callable[[Dict[str, Any]], Optional[str]],
        method: str = "GET",
        json: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Generic cursor pagination.

        ``items_key`` selects the result array on each page; ``next_from`` reads
        the next cursor (return ``None``/empty to stop); ``next_param`` is the
        request key the cursor is sent back as (query param for GET, body key for
        POST). Yields individual items.
        """
        cursor: Optional[str] = None
        while True:
            page_params = dict(params or {})
            page_json = dict(json or {})
            if cursor:
                if method == "GET":
                    page_params[next_param] = cursor
                else:
                    page_json[next_param] = cursor
            page = self.request(method, path, params=page_params or None,
                                json=page_json or None)
            for item in page.get(items_key, []) or []:
                yield item
            cursor = next_from(page)
            if not cursor:
                return


def _safe_json(resp: Any) -> Optional[Dict[str, Any]]:
    try:
        return resp.json()
    except (ValueError, AttributeError):
        return None
