"""Notion connector: block parser, property extraction, incremental poll."""

import asyncio

from connectors.notion import NotionConnector
from connectors.notion.block_parser import (
    BlockParser,
    extract_property_value,
    extract_simplified_properties,
)


class FakeNotion:
    api_calls = 5
    rate_limit_hits = 0

    def __init__(self, pages):
        self._pages = pages
        self._blocks = {
            "p1": [
                {"id": "b1", "type": "heading_1",
                 "heading_1": {"rich_text": [{"plain_text": "Title"}]}, "has_children": False},
                {"id": "b2", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": [{"plain_text": "item"}]},
                 "has_children": True},
                {"id": "b3", "type": "code",
                 "code": {"rich_text": [{"plain_text": "x=1"}], "language": "python"},
                 "has_children": False},
            ],
            "b2": [{"id": "b2a", "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "nested"}]}, "has_children": False}],
        }

    def get_all_blocks(self, block_id):
        return self._blocks.get(block_id, [])

    def search_all(self, query="", filter_type=None):
        yield from self._pages

    def query_database_all(self, db_id):
        return iter([])


def _page(pid, edited):
    return {"object": "page", "id": pid, "url": f"http://n/{pid}",
            "created_time": "2024-01-01T00:00:00.000Z", "last_edited_time": edited,
            "parent": {"type": "workspace"},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "My Page"}]}},
            "created_by": {"id": "u1", "type": "person", "person": {"email": "a@b.io"},
                           "name": "Al"},
            "last_edited_by": {"id": "u1"}}


def test_block_parser_flattens_nested_and_code():
    bp = BlockParser(FakeNotion([]), client_logger())
    content = bp.extract_page_content("p1")
    assert "Title" in content
    assert "- item" in content
    assert "nested" in content              # recursive child
    assert "```python" in content
    assert bp.blocks_fetched == 4           # 3 top + 1 nested


def client_logger():
    from connectors.common import StructuredLogger
    return StructuredLogger(console_output=False)


def test_property_extraction_types():
    assert extract_property_value({"type": "checkbox", "checkbox": True}) == "Yes"
    assert extract_property_value({"type": "multi_select",
                                   "multi_select": [{"name": "a"}, {"name": "b"}]}) == "a, b"
    assert extract_property_value({"type": "date",
                                   "date": {"start": "2024-01-01", "end": "2024-01-02"}}) == \
        "2024-01-01 - 2024-01-02"
    simplified = extract_simplified_properties({"S": {"type": "select", "select": {"name": "x"}}})
    assert simplified == {"S": "x"}


def test_poll_incremental_by_last_edited_time():
    client = FakeNotion([_page("p1", "2024-06-01T10:00:00.000Z")])
    c = NotionConnector(tenant_id="t", client=client)
    sigs = asyncio.run(c.poll())
    assert len(sigs) == 1
    s = sigs[0]
    assert s.entity == "page" and s.key == {"page_id": "p1"}
    assert s.data["title"] == "My Page"
    assert "Title" in s.data["content"]
    # cursor advanced; re-poll emits nothing (already-seen edit time)
    assert asyncio.run(c.poll()) == []


def test_scrape_to_writer(tmp_path):
    from connectors.common import OutputWriter
    client = FakeNotion([_page("p1", "2024-06-01T10:00:00.000Z")])
    c = NotionConnector(tenant_id="t", client=client)
    w = OutputWriter(str(tmp_path), source="notion", entity="pages")
    m = c.scrape(writer=w)
    assert m.records_total == 1
    assert m.extra["blocks_fetched"] >= 3
    assert (tmp_path / "notion.jsonl").exists()
