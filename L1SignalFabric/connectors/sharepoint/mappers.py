"""Pure mappers: SharePoint Graph resources → canonical SHAREPOINT SignalEvents.

Two entities:
  * ``drive_item`` — a document-library / OneDrive file or folder.
  * ``list_item``  — a SharePoint list row (custom ``fields``).

Metadata only (name, path, web URL, size, author, timestamps); file content is
never fetched. ``@removed`` tombstones map to a DELTA carrying ``deleted=True``.
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


def _user(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    u = (node or {}).get("user") or {}
    if not u:
        return None
    return {"id": u.get("id"), "name": u.get("displayName"), "email": u.get("email")}


def drive_item_to_signal(item: Dict[str, Any], tenant_id: str,
                         drive_id: str = "") -> SignalEvent:
    removed = bool(item.get("@removed"))
    parent = item.get("parentReference", {}) or {}
    is_folder = "folder" in item
    return SignalEvent(
        entity="drive_item",
        key={"drive_id": drive_id or parent.get("driveId", ""), "item_id": item.get("id", "")},
        source_system=SourceSystem.SHAREPOINT,
        tenant_id=tenant_id,
        data={
            "name": item.get("name"),
            "web_url": item.get("webUrl"),
            "size": item.get("size"),
            "kind": "folder" if is_folder else "file",
            "mime_type": (item.get("file") or {}).get("mimeType"),
            "path": parent.get("path"),
            "created_by": _user(item.get("createdBy", {})),
            "last_modified_by": _user(item.get("lastModifiedBy", {})),
            "created_time": item.get("createdDateTime"),
            "last_modified_time": item.get("lastModifiedDateTime"),
            "deleted": removed,
        },
        timestamp=_parse_dt(item.get("lastModifiedDateTime")),
        lineage=Lineage(extraction_id=f"sharepoint-item-{item.get('id', '')}",
                        source_endpoint="sharepoint.drive.delta"),
        metadata={"schemaVersion": "1.0", "removed": removed},
    )


def list_item_to_signal(item: Dict[str, Any], tenant_id: str,
                        site_id: str = "", list_id: str = "") -> SignalEvent:
    removed = bool(item.get("@removed"))
    fields = item.get("fields", {}) or {}
    return SignalEvent(
        entity="list_item",
        key={"site_id": site_id, "list_id": list_id, "item_id": item.get("id", "")},
        source_system=SourceSystem.SHAREPOINT,
        tenant_id=tenant_id,
        data={
            "web_url": item.get("webUrl"),
            "fields": fields,
            "created_by": _user(item.get("createdBy", {})),
            "last_modified_by": _user(item.get("lastModifiedBy", {})),
            "created_time": item.get("createdDateTime"),
            "last_modified_time": item.get("lastModifiedDateTime"),
            "deleted": removed,
        },
        timestamp=_parse_dt(item.get("lastModifiedDateTime")),
        lineage=Lineage(extraction_id=f"sharepoint-listitem-{item.get('id', '')}",
                        source_endpoint="sharepoint.list.delta"),
        metadata={"schemaVersion": "1.0", "removed": removed},
    )
