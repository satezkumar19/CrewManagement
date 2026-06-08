"""Outlook connector — Microsoft Graph mail (webhook push + delta pull, metadata only)."""

from .client import OutlookClient
from .connector import OutlookConnector
from .mappers import graph_message_to_record, message_to_signal, record_to_signal

__all__ = [
    "OutlookConnector",
    "OutlookClient",
    "message_to_signal",
    "graph_message_to_record",
    "record_to_signal",
]
