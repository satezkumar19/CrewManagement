"""Pure mapper: a :class:`NotionPage` → a canonical SignalEvent.

The Notion half of the normalizer seam. ``entity`` is ``page`` for standalone
pages and ``database_item`` for database rows; ``key`` is the Notion page id so a
re-edit of the same page (live or backfilled) dedups to one identity.
"""

from __future__ import annotations

from core.signal import Lineage, SignalEvent, SourceSystem

from .models import NotionPage


def page_to_signal(page: NotionPage, tenant_id: str) -> SignalEvent:
    entity = "database_item" if page.is_database_item else "page"
    return SignalEvent(
        entity=entity,
        key={"page_id": page.page_id},
        source_system=SourceSystem.NOTION,
        tenant_id=tenant_id,
        data={
            "title": page.title,
            "url": page.url,
            "object_type": page.object_type,
            "parent_type": page.parent_type,
            "parent_id": page.parent_id,
            "created_time": page.created_time.isoformat(),
            "created_by": page.created_by.model_dump(),
            "last_edited_by": page.last_edited_by.model_dump(),
            "content": page.content,
            "properties": page.properties,
            "is_database_item": page.is_database_item,
            "database_id": page.database_id,
        },
        timestamp=page.last_edited_time,
        lineage=Lineage(
            extraction_id=f"notion-{page.page_id}",
            source_endpoint="notion.api",
        ),
        metadata={"schemaVersion": "1.0"},
    )
