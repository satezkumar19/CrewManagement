"""In-memory user-profile cache — ported from the upstream Slack scraper.

Resolves a Slack user id to a :class:`~connectors.slack.models.SlackUser`
(display name + e-mail) and caches the result — including failures — so a
backfill never calls ``users.info`` twice for the same id. Tracks hit/miss and
e-mail-resolution counters for the run metrics.
"""

from __future__ import annotations

from typing import Dict

from connectors.common import StructuredLogger

from .client import SlackClient
from .models import SlackUser


class UserCache:
    def __init__(self, client: SlackClient, logger: StructuredLogger) -> None:
        self._client = client
        self._logger = logger
        self._cache: Dict[str, SlackUser] = {}
        self.total_lookups = 0
        self.cache_hits = 0
        self.api_lookups = 0
        self.email_resolved = 0
        self.resolution_failures = 0

    def get_user(self, user_id: str) -> SlackUser:
        self.total_lookups += 1
        if user_id in self._cache:
            self.cache_hits += 1
            return self._cache[user_id]

        self.api_lookups += 1
        info = self._client.get_user_info(user_id)
        if info is None:
            self.resolution_failures += 1
            self._logger.warn("user not resolved", user=user_id)
            user = SlackUser(user_id=user_id, name=user_id, email=None)
        else:
            profile = info.get("profile", {}) or {}
            name = (info.get("name") or profile.get("display_name")
                    or profile.get("real_name") or user_id)
            email = profile.get("email")
            if email:
                self.email_resolved += 1
            user = SlackUser(user_id=user_id, name=name, email=email)

        self._cache[user_id] = user
        return user

    def preload_users(self, user_ids: list) -> None:
        for uid in set(user_ids):
            if uid not in self._cache:
                self.get_user(uid)

    def get_stats(self) -> Dict[str, int]:
        return {
            "total_lookups": self.total_lookups,
            "cache_hits": self.cache_hits,
            "api_lookups": self.api_lookups,
            "email_resolved": self.email_resolved,
            "resolution_failures": self.resolution_failures,
            "cached_users": len(self._cache),
        }
