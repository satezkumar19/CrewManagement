"""Pure mappers: SharePoint folder items → canonical SHAREPOINT SignalEvents.

The app-only folder-listing client (:meth:`SharePointClient.list_folder`) returns
a flat, normalised item shape::

    {id, name, size, modified, is_folder, mime_type, web_url}

:func:`folder_item_to_signal` maps one such item to a ``drive_item`` event.
Metadata only (name, web URL, size, kind, timestamp); file content is never
fetched during ingestion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.signal import Lineage, SignalEvent, SourceSystem


def _parse_dt(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def folder_item_to_signal(item: Dict[str, Any], tenant_id: str, *,
                          hostname: str = "", site_path: str = "",
                          folder_path: str = "") -> SignalEvent:
    """Map one normalised ``list_folder`` item to a ``drive_item`` SignalEvent."""
    is_folder = bool(item.get("is_folder"))
    item_id = item.get("id", "")
    site = f"{hostname}{site_path}".strip()
    return SignalEvent(
        entity="drive_item",
        key={"site": site, "item_id": item_id},
        source_system=SourceSystem.SHAREPOINT,
        tenant_id=tenant_id,
        data={
            "name": item.get("name"),
            "web_url": item.get("web_url"),
            "size": item.get("size"),
            "kind": "folder" if is_folder else "file",
            "mime_type": item.get("mime_type"),
            "path": folder_path,
            "last_modified_time": item.get("modified"),
            "deleted": False,
        },
        timestamp=_parse_dt(item.get("modified")),
        lineage=Lineage(extraction_id=f"sharepoint-item-{item_id}",
                        source_endpoint="sharepoint.folder.list"),
        metadata={"schemaVersion": "1.0", "removed": False},
    )
