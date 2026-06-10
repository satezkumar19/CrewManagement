"""Outlook (Microsoft Graph mail) client — app-only, unread-poll model.

A minimal Graph wrapper authenticated with the **client-credentials (app-only)**
flow via :mod:`msal`, talking to Graph over :mod:`httpx`. The app registration
must hold the ``Mail.Read`` (and, to mark-as-read, ``Mail.ReadWrite``)
**Application** permission with admin consent.

Because the flow is app-only there is no signed-in user, so every call targets a
specific mailbox by UPN (``/users/{mailbox_upn}/...``) — ``/me`` is delegated-only
and not available here.

Fetch scope is controlled by ``ingest_body``:

  * **metadata only** (default) — the ``$select`` allow-list excludes ``body`` and
    ``bodyPreview``: From/To/Cc/Subject/conversation/dates/categories only.
  * **with body** (``ingest_body=True``) — ``body`` + ``bodyPreview`` are added so
    the message content is fetched. Enabled by the server via ``EMAIL_INGEST_BODY``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
import msal

log = logging.getLogger("signalfabric.connector.outlook.client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

# Metadata-only projection — body / bodyPreview deliberately excluded.
MAIL_SELECT = ("id,from,toRecipients,ccRecipients,subject,conversationId,"
               "receivedDateTime,sentDateTime,categories,internetMessageId,isRead")
# Same allow-list plus the message content (server EMAIL_INGEST_BODY on).
MAIL_SELECT_WITH_BODY = MAIL_SELECT + ",body,bodyPreview"


class OutlookClientError(Exception):
    """Wrapper for any failure talking to Graph (auth, network, 4xx, 5xx)."""


class OutlookClient:
    """Read messages from one mailbox over Graph (app-only auth).

    Usage:
        client = OutlookClient(tenant_id, client_id, client_secret, mailbox_upn)
        for msg in client.list_unread():
            ...
            client.mark_read(msg["id"])
        client.close()
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        mailbox_upn: str,
        *,
        ingest_body: bool = False,
        request_timeout_s: float = 30.0,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.mailbox_upn = mailbox_upn
        self.ingest_body = ingest_body
        self._select = MAIL_SELECT_WITH_BODY if ingest_body else MAIL_SELECT
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        self._client = httpx.Client(timeout=request_timeout_s)
        self._api_calls = 0
        self._rate_limit_hits = 0

    # -- metrics (consumed by the CLI / scrape metrics) ---------------------

    @property
    def api_calls(self) -> int:
        return self._api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._rate_limit_hits

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        """Acquire (or fetch from msal's in-memory cache) an app-only token."""
        result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPES)
        if "access_token" not in result:
            err_code = result.get("error", "unknown")
            err_desc = result.get("error_description", "no description")
            raise OutlookClientError(
                f"Token acquisition failed [{err_code}]: {err_desc}"
            )
        return result["access_token"]

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def _track(self, response: httpx.Response) -> None:
        self._api_calls += 1
        if response.status_code == 429:
            self._rate_limit_hits += 1

    # -- public API ---------------------------------------------------------

    def list_unread(self, top: int = 50, *,
                    select: Optional[str] = None) -> List[Dict[str, Any]]:
        """List up to ``top`` unread messages, newest first."""
        url = f"{GRAPH_BASE}/users/{self.mailbox_upn}/messages"
        params = {
            "$filter": "isRead eq false",
            "$select": select or self._select,
            "$orderby": "receivedDateTime desc",
            "$top": str(top),
        }
        r = self._client.get(url, headers=self._headers(), params=params)
        self._track(r)
        if r.status_code != 200:
            raise OutlookClientError(
                f"list_unread failed [{r.status_code}]: {r.text[:400]}"
            )
        return r.json().get("value", [])

    def get_message(self, message_id: str, *,
                    select: Optional[str] = None) -> Dict[str, Any]:
        """Full message metadata (+ body when ``ingest_body``) for one id."""
        url = f"{GRAPH_BASE}/users/{self.mailbox_upn}/messages/{message_id}"
        r = self._client.get(url, headers=self._headers(),
                             params={"$select": select or self._select})
        self._track(r)
        if r.status_code != 200:
            raise OutlookClientError(
                f"get_message failed [{r.status_code}]: {r.text[:400]}"
            )
        return r.json()

    def mark_read(self, message_id: str) -> None:
        """Flip a message's isRead flag to true (requires Mail.ReadWrite)."""
        url = f"{GRAPH_BASE}/users/{self.mailbox_upn}/messages/{message_id}"
        r = self._client.patch(
            url,
            headers=self._headers({"Content-Type": "application/json"}),
            json={"isRead": True},
        )
        self._track(r)
        if r.status_code not in (200, 204):
            raise OutlookClientError(
                f"mark_read failed [{r.status_code}]: {r.text[:400]}"
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OutlookClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
