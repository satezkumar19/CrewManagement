"""Pluggable source connectors for L1 SignalFabric.

Every connector implements ``core.EventStreamConnector`` and emits the canonical
:class:`~core.signal.SignalEvent`. Two ingestion shapes:

* **push** (verify + ingest, mounted on an HTTP route): Slack Events,
  Gmail Pub/Sub, Outlook & SharePoint Graph webhooks.
* **pull** (watermark-checkpointed ``poll``): Slack Web-API backfill, Notion,
  SharePoint delta, ERP and the generic Database CDC/outbox.

The real API clients live beside each connector (``client.py``) and reuse the
shared rate-limit/retry/secrets/output infrastructure in :mod:`connectors.common`.
"""

from .database import DatabaseConnector
from .erp import ErpConnector
from .gmail import GmailConnector
from .notion import NotionConnector
from .outlook import OutlookConnector
from .sharepoint import SharePointConnector
from .slack import SlackBackfillConnector, SlackConnector

__all__ = [
    "SlackConnector",
    "SlackBackfillConnector",
    "NotionConnector",
    "GmailConnector",
    "OutlookConnector",
    "SharePointConnector",
    "DatabaseConnector",
    "ErpConnector",
]
