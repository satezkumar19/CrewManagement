"""SharePoint connector — Microsoft Graph (app-only folder listing, metadata only)."""

from .client import SharePointClient, SharePointClientError
from .connector import SharePointConnector
from .mappers import folder_item_to_signal

__all__ = [
    "SharePointConnector",
    "SharePointClient",
    "SharePointClientError",
    "folder_item_to_signal",
]
