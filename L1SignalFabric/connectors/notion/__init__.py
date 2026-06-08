"""Notion connector — pull over pages, databases and blocks.

The L1 realization of the Conduit Notion scraper: real API client, full block
parser (25+ block types, recursive), database-property extraction, and a
watermark-checkpointed :class:`NotionConnector` emitting canonical SignalEvents.
"""

from .block_parser import (
    BlockParser,
    extract_properties_as_text,
    extract_simplified_properties,
)
from .client import NotionClient
from .connector import NotionConnector
from .mappers import page_to_signal
from .models import NotionPage, NotionUser

__all__ = [
    "NotionConnector",
    "NotionClient",
    "BlockParser",
    "NotionPage",
    "NotionUser",
    "page_to_signal",
    "extract_properties_as_text",
    "extract_simplified_properties",
]
