"""Gmail API client — on the shared rate-limited HTTP spine.

Talks directly to the Gmail REST API (``gmail.googleapis.com/gmail/v1``) with a
bearer access token, so no ``google-api-python-client`` dependency is needed.

Fetch scope is controlled by ``ingest_body``:

  * **metadata only** (default) — ``format=metadata`` with an explicit allow-list
    of headers: From/To/Cc/Subject/Date + thread + labels, never content. This is
    the privacy boundary the L1 plan mandates for Gmail.
  * **full** (``ingest_body=True``) — ``format=full`` so the message body is
    fetched too (decoded downstream in the mapper). Enabled by the server via the
    ``EMAIL_INGEST_BODY`` setting.

Endpoints: users.watch · users.history.list · users.messages.get ·
users.messages.list · users.getProfile
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from connectors.common import RateLimitedClient, StructuredLogger

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
METADATA_HEADERS = ["From", "To", "Cc", "Subject", "Date", "Message-ID"]


class GmailClient:
    def __init__(
        self,
        access_token: Union[str, Callable[[], str]],
        logger: Optional[StructuredLogger] = None,
        *,
        user_id: str = "me",
        rate_limit_delay_ms: int = 200,
        max_rate_limit_errors: int = 3,
        ingest_body: bool = False,
    ) -> None:
        self.logger = logger or StructuredLogger(console_output=False)
        self.user_id = user_id
        self.ingest_body = ingest_body
        # ``access_token`` may be a literal bearer string (short-lived, e.g. an
        # OAuth Playground token) or a callable that returns a fresh token on
        # demand (the refresh-token flow — see connectors.gmail.auth). Normalise
        # to a provider so the Authorization header is re-minted per request and
        # a refreshed token is picked up transparently.
        token_provider = access_token if callable(access_token) else (lambda: access_token)
        self._http = RateLimitedClient(
            base_url=GMAIL_API_BASE,
            logger=self.logger,
            auth_provider=token_provider,
            rate_limit_delay_ms=rate_limit_delay_ms,
            max_rate_limit_errors=max_rate_limit_errors,
        )

    @property
    def api_calls(self) -> int:
        return self._http.api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._http.rate_limit_hits

    def _u(self, path: str) -> str:
        return f"users/{self.user_id}/{path}"

    # --- watch registration (push setup) ---
    def watch(self, topic_name: str, label_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"topicName": topic_name}
        if label_ids:
            body["labelIds"] = label_ids
        return self._http.post(self._u("watch"), json=body)

    def get_profile(self) -> Dict[str, Any]:
        return self._http.get(self._u("profile"))

    # --- history (the push expansion path) ---
    def history_list(self, start_history_id: str,
                     history_types: Optional[List[str]] = None) -> Iterator[Dict[str, Any]]:
        """Yield history records added since ``start_history_id`` (paginated)."""
        # requests encodes a list value as repeated query params, which is what
        # the Gmail API expects for historyTypes.
        params: Dict[str, Any] = {
            "startHistoryId": start_history_id,
            "historyTypes": history_types or ["messageAdded"],
        }
        page_token: Optional[str] = None
        while True:
            p = dict(params)
            if page_token:
                p["pageToken"] = page_token
            data = self._http.get(self._u("history"), params=p)
            yield from data.get("history", [])
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    def list_messages(self, query: str = "", max_results: int = 100) -> Iterator[Dict[str, Any]]:
        params: Dict[str, Any] = {"maxResults": min(max_results, 500)}
        if query:
            params["q"] = query
        page_token: Optional[str] = None
        while True:
            p = dict(params)
            if page_token:
                p["pageToken"] = page_token
            data = self._http.get(self._u("messages"), params=p)
            yield from data.get("messages", [])
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """Fetch a single message.

        ``format=full`` (headers + decoded body parts) when ``ingest_body`` is on,
        else ``format=metadata`` with the header allow-list (no body).
        """
        if self.ingest_body:
            params: Dict[str, Any] = {"format": "full"}
        else:
            params = {"format": "metadata", "metadataHeaders": METADATA_HEADERS}
        return self._http.get(self._u(f"messages/{message_id}"), params=params)

    # Back-compat alias for callers that requested metadata explicitly.
    get_message_metadata = get_message
