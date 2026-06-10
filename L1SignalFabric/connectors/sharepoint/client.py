"""SharePoint (Microsoft Graph) client — app-only, folder-listing model.

A minimal Graph wrapper authenticated with the **client-credentials (app-only)**
flow via :mod:`msal`, talking to Graph over :mod:`httpx`. The app must hold
``Sites.Read.All`` (or ``Sites.Selected`` scoped to this site) with admin
consent.

One site is addressed by ``hostname`` + ``site_path`` (e.g.
``thinkpalm.sharepoint.com`` + ``/sites/freight-demo``). The site's default
document library is resolved lazily; :meth:`list_folder` then lists the immediate
children of a path under the drive root. Metadata only — file content is fetched
only on explicit :meth:`download_file`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
import msal

log = logging.getLogger("signalfabric.connector.sharepoint.client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

FOLDER_SELECT = "id,name,size,lastModifiedDateTime,file,folder,webUrl"


class SharePointClientError(Exception):
    """Wrapper for any failure talking to SharePoint via Graph."""


class SharePointClient:
    """Read folders + files from one SharePoint site over Graph (app-only auth).

    Usage:
        sp = SharePointClient(tenant_id, client_id, client_secret,
                              hostname='thinkpalm.sharepoint.com',
                              site_path='/sites/freight-demo')
        for item in sp.list_folder('Shared Documents/crew'):
            print(item['name'], item['size'])
        sp.close()
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        hostname: str,
        site_path: str,
        *,
        request_timeout_s: float = 30.0,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.hostname = hostname.strip().rstrip("/")
        self.site_path = "/" + site_path.strip().strip("/")  # leading slash, no trailing
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        self._client = httpx.Client(timeout=request_timeout_s)
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None
        self._api_calls = 0
        self._rate_limit_hits = 0

    # -- metrics ------------------------------------------------------------

    @property
    def api_calls(self) -> int:
        return self._api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._rate_limit_hits

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPES)
        if "access_token" not in result:
            err_code = result.get("error", "unknown")
            err_desc = result.get("error_description", "no description")
            raise SharePointClientError(
                f"Token acquisition failed [{err_code}]: {err_desc}"
            )
        return result["access_token"]

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    def _track(self, response: httpx.Response) -> None:
        self._api_calls += 1
        if response.status_code == 429:
            self._rate_limit_hits += 1

    # -- site / drive resolution -------------------------------------------

    def resolve_site_id(self) -> str:
        """Look up Graph's internal site id for this hostname + path."""
        if self._site_id:
            return self._site_id
        # Graph syntax: /sites/{hostname}:{site-path}
        url = f"{GRAPH_BASE}/sites/{self.hostname}:{self.site_path}"
        r = self._client.get(url, headers=self._headers())
        self._track(r)
        if r.status_code != 200:
            raise SharePointClientError(
                f"site lookup failed [{r.status_code}] for "
                f"{self.hostname}{self.site_path}: {r.text[:400]}"
            )
        self._site_id = r.json()["id"]
        return self._site_id

    def resolve_drive_id(self) -> str:
        """Default document library drive id for the site."""
        if self._drive_id:
            return self._drive_id
        site_id = self.resolve_site_id()
        url = f"{GRAPH_BASE}/sites/{site_id}/drive"
        r = self._client.get(url, headers=self._headers())
        self._track(r)
        if r.status_code != 200:
            raise SharePointClientError(
                f"drive lookup failed [{r.status_code}]: {r.text[:400]}"
            )
        self._drive_id = r.json()["id"]
        return self._drive_id

    # -- public API ---------------------------------------------------------

    def list_folder(self, folder_path: str) -> List[Dict[str, Any]]:
        """List immediate children of ``folder_path`` (relative to drive root).

        ``folder_path`` is a normal path with spaces (e.g. 'Shared Documents/crew').
        An empty folder returns ``[]``; a missing folder raises
        :class:`SharePointClientError`. Each item is normalised to a flat shape:
        ``{id, name, size, modified, is_folder, mime_type, web_url}``.
        """
        site_id = self.resolve_site_id()
        # Normalise and URL-encode each path segment (preserve slashes).
        encoded = quote(folder_path.strip().strip("/"), safe="/")
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{encoded}:/children"
        params = {"$select": FOLDER_SELECT, "$top": "200"}
        r = self._client.get(url, headers=self._headers(), params=params)
        self._track(r)
        if r.status_code == 404:
            raise SharePointClientError(
                f"folder not found: {folder_path} "
                f"(site={self.hostname}{self.site_path})"
            )
        if r.status_code != 200:
            raise SharePointClientError(
                f"list_folder failed [{r.status_code}]: {r.text[:400]}"
            )
        items = r.json().get("value", [])
        result: List[Dict[str, Any]] = []
        for it in items:
            result.append({
                "id":        it["id"],
                "name":      it["name"],
                "size":      it.get("size", 0),
                "modified":  it.get("lastModifiedDateTime"),
                "is_folder": "folder" in it,
                "mime_type": (it.get("file") or {}).get("mimeType"),
                "web_url":   it.get("webUrl"),
            })
        return result

    def get_file_metadata(self, item_id: str) -> Dict[str, Any]:
        site_id = self.resolve_site_id()
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}"
        r = self._client.get(url, headers=self._headers())
        self._track(r)
        if r.status_code != 200:
            raise SharePointClientError(
                f"get_file_metadata failed [{r.status_code}]: {r.text[:400]}"
            )
        return r.json()

    def download_file(self, item_id: str) -> bytes:
        """Return raw bytes for the file (metadata-only ingestion never calls this)."""
        site_id = self.resolve_site_id()
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"
        r = self._client.get(url, headers=self._headers(), follow_redirects=True)
        self._track(r)
        if r.status_code != 200:
            raise SharePointClientError(
                f"download_file failed [{r.status_code}]: {r.text[:200]}"
            )
        return r.content

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SharePointClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
