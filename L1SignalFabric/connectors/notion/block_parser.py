"""Parse Notion blocks and extract plain text content.

Ported verbatim (behaviour-for-behaviour) from the upstream Notion scraper's
``block_parser.py`` so the L1 connector captures exactly the same flattened page
content and database-property text. Handles 25+ block types, recursive nested
blocks (max depth 10), rich-text flattening, and the full database property-type
matrix.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from connectors.common import StructuredLogger

from .client import NotionClient


class BlockParser:
    """Recursively parses Notion blocks and extracts plain text content."""

    def __init__(self, client: NotionClient, logger: StructuredLogger) -> None:
        self.client = client
        self.logger = logger
        self.blocks_fetched = 0

    def extract_page_content(self, page_id: str) -> str:
        """Extract all flattened text content from a page (incl. nested blocks)."""
        blocks = self.client.get_all_blocks(page_id)
        self.blocks_fetched += len(blocks)

        content_parts: List[str] = []
        for block in blocks:
            text = self._extract_block_text(block)
            if text:
                content_parts.append(text)
            if block.get("has_children", False):
                child_content = self._extract_children_content(block["id"])
                if child_content:
                    content_parts.append(child_content)
        return "\n".join(content_parts)

    def _extract_children_content(self, block_id: str, depth: int = 0) -> str:
        if depth > 10:  # prevent infinite recursion
            return ""
        try:
            blocks = self.client.get_all_blocks(block_id)
            self.blocks_fetched += len(blocks)
        except Exception as e:  # noqa: BLE001
            self.logger.warn("Failed to get child blocks", block_id=block_id, error=str(e))
            return ""

        content_parts: List[str] = []
        for block in blocks:
            text = self._extract_block_text(block, indent=depth)
            if text:
                content_parts.append(text)
            if block.get("has_children", False):
                child_content = self._extract_children_content(block["id"], depth + 1)
                if child_content:
                    content_parts.append(child_content)
        return "\n".join(content_parts)

    def _extract_block_text(self, block: Dict[str, Any], indent: int = 0) -> Optional[str]:
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        prefix = "  " * indent if indent > 0 else ""

        if block_type in ["paragraph", "quote", "callout"]:
            return prefix + self._extract_rich_text(block_data.get("rich_text", []))
        elif block_type in ["heading_1", "heading_2", "heading_3"]:
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{prefix}{text}"
        elif block_type == "bulleted_list_item":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{prefix}- {text}"
        elif block_type == "numbered_list_item":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{prefix}* {text}"
        elif block_type == "to_do":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            checkbox = "[x]" if block_data.get("checked", False) else "[ ]"
            return f"{prefix}{checkbox} {text}"
        elif block_type == "toggle":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{prefix}> {text}"
        elif block_type == "code":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            language = block_data.get("language", "")
            return f"{prefix}```{language}\n{text}\n```"
        elif block_type == "equation":
            return prefix + block_data.get("expression", "")
        elif block_type == "divider":
            return f"{prefix}---"
        elif block_type == "table_row":
            cells = block_data.get("cells", [])
            cell_texts = [self._extract_rich_text(cell) for cell in cells]
            return prefix + " | ".join(cell_texts)
        elif block_type == "bookmark":
            url = block_data.get("url", "")
            caption = self._extract_rich_text(block_data.get("caption", []))
            return f"{prefix}[{caption}]({url})" if caption else f"{prefix}{url}"
        elif block_type == "link_preview":
            return prefix + block_data.get("url", "")
        elif block_type == "embed":
            return prefix + block_data.get("url", "")
        elif block_type in ["image", "video", "file", "pdf"]:
            caption = self._extract_rich_text(block_data.get("caption", []))
            file_info = block_data.get("file", {}) or block_data.get("external", {})
            url = file_info.get("url", "")
            if caption:
                return f"{prefix}[{block_type}: {caption}]"
            elif url:
                return f"{prefix}[{block_type}]"
            return None
        elif block_type == "child_page":
            return f"{prefix}[Page: {block_data.get('title', '')}]"
        elif block_type == "child_database":
            return f"{prefix}[Database: {block_data.get('title', '')}]"
        elif block_type == "synced_block":
            return None  # content fetched via has_children
        elif block_type == "column_list":
            return None  # content in child columns
        elif block_type == "column":
            return None  # content in child blocks
        elif block_type == "table":
            return None  # content in table_row children
        elif block_type == "table_of_contents":
            return f"{prefix}[Table of Contents]"
        elif block_type == "breadcrumb":
            return None
        elif block_type == "template":
            text = self._extract_rich_text(block_data.get("rich_text", []))
            return f"{prefix}[Template: {text}]" if text else None
        elif block_type == "link_to_page":
            page_id = block_data.get("page_id", "") or block_data.get("database_id", "")
            return f"{prefix}[Link to: {page_id}]"
        elif block_type == "unsupported":
            return None
        else:
            if "rich_text" in block_data:
                return prefix + self._extract_rich_text(block_data["rich_text"])
            self.logger.debug("Unknown block type", type=block_type)
            return None

    def _extract_rich_text(self, rich_text: List[Dict[str, Any]]) -> str:
        if not rich_text:
            return ""
        return "".join(item.get("plain_text", "") for item in rich_text if item.get("plain_text"))


def extract_properties_as_text(properties: Dict[str, Any]) -> str:
    """Convert Notion database properties to a flat ``Name: value`` text block."""
    if not properties:
        return ""
    lines = []
    for prop_name, prop_data in properties.items():
        value = extract_property_value(prop_data)
        if value:
            lines.append(f"{prop_name}: {value}")
    return "\n".join(lines)


def extract_property_value(prop_data: Dict[str, Any]) -> Optional[str]:
    """Extract the textual value of a single Notion property (all 20+ types)."""
    prop_type = prop_data.get("type", "")

    if prop_type == "title":
        return "".join(i.get("plain_text", "") for i in prop_data.get("title", []))
    elif prop_type == "rich_text":
        return "".join(i.get("plain_text", "") for i in prop_data.get("rich_text", []))
    elif prop_type == "number":
        return str(prop_data.get("number")) if prop_data.get("number") is not None else None
    elif prop_type == "select":
        select = prop_data.get("select")
        return select.get("name") if select else None
    elif prop_type == "multi_select":
        options = prop_data.get("multi_select", [])
        return ", ".join(o.get("name", "") for o in options) if options else None
    elif prop_type == "status":
        status = prop_data.get("status")
        return status.get("name") if status else None
    elif prop_type == "date":
        date_obj = prop_data.get("date")
        if date_obj:
            start = date_obj.get("start", "")
            end = date_obj.get("end")
            return f"{start} - {end}" if end else start
        return None
    elif prop_type == "checkbox":
        return "Yes" if prop_data.get("checkbox") else "No"
    elif prop_type == "url":
        return prop_data.get("url")
    elif prop_type == "email":
        return prop_data.get("email")
    elif prop_type == "phone_number":
        return prop_data.get("phone_number")
    elif prop_type == "people":
        people = prop_data.get("people", [])
        return ", ".join(p.get("name", p.get("id", "")) for p in people) if people else None
    elif prop_type == "files":
        files = prop_data.get("files", [])
        return ", ".join(f.get("name", "") for f in files) if files else None
    elif prop_type == "relation":
        relations = prop_data.get("relation", [])
        return f"({len(relations)} related items)" if relations else None
    elif prop_type == "rollup":
        rollup = prop_data.get("rollup", {})
        rollup_type = rollup.get("type", "")
        if rollup_type == "number":
            return str(rollup.get("number"))
        elif rollup_type == "array":
            return f"({len(rollup.get('array', []))} items)"
        return None
    elif prop_type == "formula":
        formula = prop_data.get("formula", {})
        return str(formula.get(formula.get("type", "")))
    elif prop_type == "created_time":
        return prop_data.get("created_time")
    elif prop_type == "created_by":
        user = prop_data.get("created_by", {})
        return user.get("name", user.get("id", ""))
    elif prop_type == "last_edited_time":
        return prop_data.get("last_edited_time")
    elif prop_type == "last_edited_by":
        user = prop_data.get("last_edited_by", {})
        return user.get("name", user.get("id", ""))
    elif prop_type == "unique_id":
        uid = prop_data.get("unique_id", {})
        prefix = uid.get("prefix", "")
        number = uid.get("number", "")
        return f"{prefix}{number}" if prefix else str(number)
    return None


def extract_simplified_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    """Property name → simplified value, dropping Nones (for metadata storage)."""
    if not properties:
        return {}
    result: Dict[str, Any] = {}
    for prop_name, prop_data in properties.items():
        value = extract_property_value(prop_data)
        if value is not None:
            result[prop_name] = value
    return result
