"""Slack connector — live Events API (push) + Web API backfill (pull).

* :class:`SlackConnector` — the live push connector mounted at ``/slack/events``
  (signature verify + event normalization), wired into the FastAPI app.
* :class:`SlackBackfillConnector` — the Web-API pull connector (the realization
  of the upstream Slack scraper): channel history + threads + user/reaction
  resolution, watermark-checkpointed, emitting the same canonical events.
"""

from .backfill import SlackBackfillConfig, SlackBackfillConnector
from .client import SlackApiError, SlackClient
from .connector import SlackConnector
from .models import ChannelInfo, SlackMessage, SlackReaction, SlackUser
from .user_cache import UserCache
from .verify import verify_slack_signature

__all__ = [
    "SlackConnector",
    "SlackBackfillConnector",
    "SlackBackfillConfig",
    "SlackClient",
    "SlackApiError",
    "UserCache",
    "SlackUser",
    "SlackMessage",
    "SlackReaction",
    "ChannelInfo",
    "verify_slack_signature",
]
