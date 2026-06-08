"""Shared infrastructure for the real source connectors.

Everything the Conduit Slack/Notion scrapers hand-rolled per-source — structured
logging, secret resolution, rate-limited+retrying HTTP, Conduit-compatible
output, run metrics, watermark wiring — lives here once and is reused by every
connector (Slack, Notion, Gmail, Outlook, SharePoint, Database).
"""

from .graph import GraphClient
from .http import HTTPError, RateLimitedClient, RateLimitError
from .logger import StructuredLogger
from .metrics import ScrapeMetrics
from .msgraph_webhook import notification_items, verify_graph_webhook
from .poller import PollingConnector
from .secrets import get_secret_value, parse_timestamp, resolve_token
from .writer import OutputWriter

__all__ = [
    "StructuredLogger",
    "RateLimitedClient",
    "RateLimitError",
    "HTTPError",
    "ScrapeMetrics",
    "OutputWriter",
    "PollingConnector",
    "GraphClient",
    "verify_graph_webhook",
    "notification_items",
    "resolve_token",
    "get_secret_value",
    "parse_timestamp",
]
