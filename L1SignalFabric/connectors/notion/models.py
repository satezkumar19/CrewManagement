"""Notion domain models — ported from the Conduit Notion scraper."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class NotionUser(BaseModel):
    id: str
    name: Optional[str] = None
    email: Optional[str] = None


class NotionPage(BaseModel):
    page_id: str
    object_type: str                       # "page" | "database_item"
    title: str = "Untitled"
    url: str = ""
    parent_type: str = ""                  # "workspace" | "page_id" | "database_id"
    parent_id: str = ""
    created_time: datetime
    last_edited_time: datetime
    created_by: NotionUser
    last_edited_by: NotionUser
    content: str = ""                      # flattened block text
    properties: Optional[Dict[str, Any]] = None
    is_database_item: bool = False
    database_id: Optional[str] = None

    def to_jsonl_dict(self) -> dict:
        def _iso(dt: datetime) -> str:
            return dt.isoformat() + "Z" if dt.tzinfo is None else dt.isoformat()

        return {
            "page_id": self.page_id,
            "object_type": self.object_type,
            "title": self.title,
            "url": self.url,
            "parent_type": self.parent_type,
            "parent_id": self.parent_id,
            "created_time": _iso(self.created_time),
            "last_edited_time": _iso(self.last_edited_time),
            "created_by": self.created_by.model_dump(),
            "last_edited_by": self.last_edited_by.model_dump(),
            "content": self.content,
            "properties": self.properties,
            "is_database_item": self.is_database_item,
            "database_id": self.database_id,
        }
