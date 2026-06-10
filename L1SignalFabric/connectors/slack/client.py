"""Slack Web API client — the real backfill/enrichment client.

Ports the upstream Slack scraper's ``client.py`` onto the shared
:class:`~connectors.common.http.RateLimitedClient`. Same endpoints, same
pagination, same rate-limit/retry semantics (default 1200 ms pacing ≈ 50 req/min,
fail after 2 consecutive 429s), implemented directly over the Slack HTTPS API so
no ``slack_sdk`` dependency is required.

Endpoints covered (parity with the scraper):
  auth.test · conversations.list · conversations.info · conversations.history ·
  conversations.replies · users.info · reactions.get
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from connectors.common import RateLimitedClient, StructuredLogger
from connectors.common.http import RateLimitError  # re-export-friendly

from .models import ChannelInfo, SlackReaction

SLACK_API_BASE = "https://slack.com/api"


class SlackApiError(Exception):
    """Slack returned ``ok: false`` (a logical error, not an HTTP failure)."""


class SlackClient:
    """Thin, rate-limited wrapper over the Slack Web API."""

    def __init__(
        self,
        token: str,
        logger: Optional[StructuredLogger] = None,
        *,
        rate_limit_delay_ms: int = 1200,
        max_rate_limit_errors: int = 2,
    ) -> None:
        self.logger = logger or StructuredLogger(console_output=False)
        self._http = RateLimitedClient(
            base_url=SLACK_API_BASE,
            logger=self.logger,
            default_headers={"Authorization": f"Bearer {token}"},
            rate_limit_delay_ms=rate_limit_delay_ms,
            max_rate_limit_errors=max_rate_limit_errors,
        )

    # --- metrics passthrough (writer/metrics read these) ---
    @property
    def api_calls(self) -> int:
        return self._http.api_calls

    @property
    def rate_limit_hits(self) -> int:
        return self._http.rate_limit_hits

    # --- low-level call: Slack signals errors via ok=false in a 200 body ---
    def _call(self, method: str, **params: Any) -> Dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        data = self._http.get(method, params=clean)
        if not data.get("ok", False):
            raise SlackApiError(f"{method}: {data.get('error', 'unknown')}")
        return data

    # --- auth ---
    def test_auth(self) -> Dict[str, Any]:
        d = self._call("auth.test")
        info = {"team": d.get("team"), "user": d.get("user"),
                "bot_id": d.get("bot_id"), "team_id": d.get("team_id"),
                "url": d.get("url")}
        self.logger.info("slack auth ok", **{k: v for k, v in info.items() if v})
        return info

    # --- channels ---
    def list_channels(self, types: str = "public_channel") -> List[ChannelInfo]:
        out: List[ChannelInfo] = []
        cursor: Optional[str] = None
        while True:
            d = self._call("conversations.list", types=types,
                           exclude_archived=True, limit=200, cursor=cursor)
            for c in d.get("channels", []):
                out.append(_channel(c))
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return out

    def get_channel_info(self, channel_id: str) -> Optional[ChannelInfo]:
        try:
            d = self._call("conversations.info", channel=channel_id)
        except SlackApiError as exc:
            self.logger.warn("conversations.info failed", channel=channel_id, error=str(exc))
            return None
        return _channel(d.get("channel", {}))

    def get_channel_history(
        self,
        channel_id: str,
        oldest: Optional[float] = None,
        latest: Optional[float] = None,
        limit: int = 200,
    ) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        fetched = 0
        while True:
            d = self._call("conversations.history", channel=channel_id,
                           oldest=oldest, latest=latest, limit=min(limit, 1000),
                           cursor=cursor)
            for m in d.get("messages", []):
                fetched += 1
                yield m
            if not d.get("has_more"):
                break
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        self.logger.debug("history fetched", channel=channel_id, messages=fetched)

    def get_thread_replies(
        self, channel_id: str, thread_ts: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while len(out) < limit:
            d = self._call("conversations.replies", channel=channel_id,
                           ts=thread_ts, limit=min(limit, 200), cursor=cursor)
            for m in d.get("messages", []):
                # the API echoes the parent as the first element — skip it
                if m.get("ts") == thread_ts and not m.get("thread_ts"):
                    continue
                out.append(m)
            if not d.get("has_more"):
                break
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return out[:limit]

    # --- users ---
    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            d = self._call("users.info", user=user_id)
        except SlackApiError as exc:
            if "user_not_found" in str(exc):
                return None
            self.logger.warn("users.info failed", user=user_id, error=str(exc))
            return None
        return d.get("user")

    # --- reactions ---
    def get_reactions(self, channel_id: str, timestamp: str) -> List[SlackReaction]:
        try:
            d = self._call("reactions.get", channel=channel_id, timestamp=timestamp)
        except SlackApiError:
            return []
        msg = d.get("message", {}) or {}
        return [SlackReaction(name=r.get("name", ""), users=r.get("users", []),
                              count=r.get("count", 0))
                for r in msg.get("reactions", [])]


def _channel(c: Dict[str, Any]) -> ChannelInfo:
    return ChannelInfo(
        id=c.get("id", ""),
        name=c.get("name", ""),
        is_member=bool(c.get("is_member", False)),
        is_private=bool(c.get("is_private", False)),
        num_members=int(c.get("num_members", 0) or 0),
    )
