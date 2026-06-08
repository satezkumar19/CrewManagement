"""SharePoint connector — Microsoft Graph drives & lists (delta pull + webhook)."""

from .client import SharePointClient
from .connector import DriveTarget, ListTarget, SharePointConnector
from .mappers import drive_item_to_signal, list_item_to_signal

__all__ = [
    "SharePointConnector",
    "SharePointClient",
    "DriveTarget",
    "ListTarget",
    "drive_item_to_signal",
    "list_item_to_signal",
]
