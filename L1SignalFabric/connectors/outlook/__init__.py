"""Outlook connector — Microsoft Graph mail (app-only unread-poll, metadata only)."""

from .client import OutlookClient, OutlookClientError
from .connector import OutlookConnector
from .mappers import graph_message_to_record, message_to_signal, record_to_signal

__all__ = [
    "OutlookConnector",
    "OutlookClient",
    "OutlookClientError",
    "message_to_signal",
    "graph_message_to_record",
    "record_to_signal",
]
