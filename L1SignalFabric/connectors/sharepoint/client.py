"""SharePoint / OneDrive (Microsoft Graph) client.

Wraps the shared :class:`~connectors.common.graph.GraphClient`. Covers the two
content shapes SharePoint exposes:

  * **drive items** (document libraries / OneDrive files) via ``/drives/{id}/root/delta``
  * **list items** (SharePoint lists) via ``/sites/{id}/lists/{id}/items/delta``

Delta queries give incremental, resumable sync (the ``@odata.deltaLink``
watermark). Captures item metadata only (name, path, size, web URL, author,
timestamps) — never file content.

Endpoints: sites · sites/{id}/drives · drives/{id}/root/delta ·
sites/{id}/lists · sites/{id}/lists/{id}/items/delta · subscriptions
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from connectors.common import GraphClient

DRIVE_ITEM_SELECT = "id,name,webUrl,size,createdDateTime,lastModifiedDateTime,createdBy,lastModifiedBy,parentReference,file,folder"


class SharePointClient:
    def __init__(self, graph: GraphClient) -> None:
        self.graph = graph

    @property
    def api_calls(self) -> int:
        return self.graph.api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self.graph.rate_limit_hits

    # --- discovery ---
    def get_site(self, hostname: str, site_path: str) -> Dict[str, Any]:
        """Resolve a site by ``hostname`` + server-relative ``/sites/<name>`` path."""
        return self.graph.get(f"sites/{hostname}:{site_path}")

    def list_site_drives(self, site_id: str) -> List[Dict[str, Any]]:
        return list(self.graph.iter_collection(f"sites/{site_id}/drives"))

    def list_site_lists(self, site_id: str) -> List[Dict[str, Any]]:
        return list(self.graph.iter_collection(f"sites/{site_id}/lists"))

    def get_default_drive(self, site_id: str) -> Dict[str, Any]:
        return self.graph.get(f"sites/{site_id}/drive")

    # --- delta sync ---
    def drive_delta(self, drive_id: str, *, start: Optional[str] = None,
                    ) -> Tuple[List[Dict[str, Any]], str]:
        if start:
            return self.graph.delta(start)
        return self.graph.delta(f"drives/{drive_id}/root/delta",
                                params={"$select": DRIVE_ITEM_SELECT})

    def list_delta(self, site_id: str, list_id: str, *, start: Optional[str] = None,
                   ) -> Tuple[List[Dict[str, Any]], str]:
        if start:
            return self.graph.delta(start)
        return self.graph.delta(f"sites/{site_id}/lists/{list_id}/items/delta",
                                params={"$expand": "fields"})

    # --- subscriptions (webhook lifecycle) ---
    def create_subscription(self, *, notification_url: str, client_state: str,
                            resource: str, expiration_iso: str,
                            change_type: str = "updated") -> Dict[str, Any]:
        return self.graph.post("subscriptions", json={
            "changeType": change_type,
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expiration_iso,
            "clientState": client_state,
        })

    def delete_subscription(self, subscription_id: str) -> None:
        self.graph.delete(f"subscriptions/{subscription_id}")
