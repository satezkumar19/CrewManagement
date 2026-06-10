"""Microsoft Graph client — shared by the Outlook and SharePoint connectors.

Both Microsoft 365 sources speak the same API surface (``graph.microsoft.com``),
the same OAuth2 (Azure AD), the same ``@odata.nextLink`` collection paging and
the same ``@odata.deltaLink`` incremental-sync model — so it lives here once.

Auth: supply a bearer ``access_token`` directly, **or** app credentials
(``tenant_id`` + ``client_id`` + ``client_secret``) for the client-credentials
grant, which this acquires lazily via the token endpoint (using ``requests``; no
``msal`` dependency).
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from .http import RateLimitedClient
from .logger import StructuredLogger

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTH_TMPL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class GraphClient:
    def __init__(
        self,
        *,
        access_token: str = "",
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        scope: str = "https://graph.microsoft.com/.default",
        logger: Optional[StructuredLogger] = None,
        rate_limit_delay_ms: int = 100,
        max_rate_limit_errors: int = 3,
    ) -> None:
        self.logger = logger or StructuredLogger(console_output=False)
        self._token = access_token
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._http = RateLimitedClient(
            base_url=GRAPH_BASE,
            logger=self.logger,
            rate_limit_delay_ms=rate_limit_delay_ms,
            max_rate_limit_errors=max_rate_limit_errors,
        )
        self._apply_token()

    def _apply_token(self) -> None:
        if self._token:
            self._http.default_headers["Authorization"] = f"Bearer {self._token}"

    def _ensure_token(self) -> None:
        if self._token:
            return
        if not (self._tenant_id and self._client_id and self._client_secret):
            raise RuntimeError("Graph: provide access_token or tenant/client/secret")
        import requests  # lazy
        resp = requests.post(
            _AUTH_TMPL.format(tenant=self._tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": self._scope,
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._apply_token()

    @property
    def api_calls(self) -> int:
        return self._http.api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._http.rate_limit_hits

    # --- requests ---
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._ensure_token()
        return self._http.get(path, params=params)

    def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._ensure_token()
        return self._http.post(path, json=json)

    def patch(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._ensure_token()
        return self._http.request("PATCH", path, json=json)

    def delete(self, path: str) -> Dict[str, Any]:
        self._ensure_token()
        return self._http.request("DELETE", path)

    # --- collection paging (@odata.nextLink) ---
    def iter_collection(self, path: str,
                        params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        page = self.get(path, params=params)
        while True:
            yield from page.get("value", [])
            nxt = page.get("@odata.nextLink")
            if not nxt:
                return
            page = self.get(nxt)

    # --- delta sync (@odata.deltaLink) ---
    def delta(self, start: str,
              params: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], str]:
        """Drain a delta query, following nextLinks; return (items, delta_link).

        ``start`` is either a relative delta path (cold start) or a saved
        ``@odata.deltaLink`` (incremental). The returned delta link is the
        watermark to persist for the next poll.
        """
        items: List[Dict[str, Any]] = []
        page = self.get(start, params=params)
        while True:
            items.extend(page.get("value", []))
            nxt = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink")
            if nxt:
                page = self.get(nxt)
                continue
            return items, delta_link or start
