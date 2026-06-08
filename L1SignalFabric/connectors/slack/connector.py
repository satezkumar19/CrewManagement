"""Slack Events API connector (Day-1 skeleton).

Push connector. The FastAPI ``/slack/events`` route hands every inbound request
to :meth:`verify` and, when authentic, to :meth:`ingest`.

Day-1 scope (this skeleton):
  * ``url_verification`` challenge handshake — fully working.
  * signature verification (HMAC) — working, with a dev bypass when no signing
    secret is configured.
  * ``event_callback`` fan-out to pure mappers for message / reaction_added /
    member_joined_channel.
  * idempotent de-dup hint via Slack ``event_id``.

Day-2 (Sreekumar) extends: Socket Mode fallback, full event coverage, retry
semantics on ``X-Slack-Retry-Num``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from core.connector import EventStreamConnector, InboundRequest, VerifyResult
from core.signal import SignalEvent, SourceSystem

from .mappers import MAPPERS
from .verify import verify_slack_signature

logger = logging.getLogger("signalfabric.connector.slack")


class SlackConnector(EventStreamConnector):
    name = "slack"
    source_system = SourceSystem.SLACK

    def __init__(
        self,
        *,
        tenant_id: str,
        signing_secret: str = "",
        dev_allow_unverified: bool = True,
        replay_window_sec: int = 300,
        token: str = "",
        client: Any = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._signing_secret = signing_secret
        self._dev_allow_unverified = dev_allow_unverified
        self._replay_window_sec = replay_window_sec
        # in-memory seen-set of Slack event_ids (Day-1 dedup; Day-2 → bus dedup)
        self._seen_event_ids: set[str] = set()
        # optional Web-API enrichment: resolve channel/user ids → human names
        # (needs a bot token with channels:read + users:read). Cached per id.
        self._token = token
        self._client = client
        self._users: Any = None
        self._channel_names: dict[str, str] = {}
        self._user_names: dict[str, str] = {}

    # ------------------------------------------------------------------ verify
    def verify(self, request: InboundRequest) -> VerifyResult:
        body = request.json or {}

        # 1) URL verification handshake — Slack sends this once when you set the
        #    Request URL. Echo the challenge; do not ingest.
        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            logger.info("slack url_verification handshake")
            return VerifyResult.challenge_with(challenge)

        # 2) Signature verification.
        if self._signing_secret:
            check = verify_slack_signature(
                signing_secret=self._signing_secret,
                timestamp=request.header("X-Slack-Request-Timestamp"),
                body=request.body,
                signature=request.header("X-Slack-Signature"),
                replay_window_sec=self._replay_window_sec,
            )
            if not check.ok:
                return VerifyResult.reject(check.reason)
            return VerifyResult.ok()

        # 3) No secret configured: dev/demo bypass (or reject if disabled).
        if self._dev_allow_unverified:
            logger.warning("slack signature NOT verified (dev mode, no signing secret)")
            return VerifyResult.ok()
        return VerifyResult.reject("no signing secret configured")

    # ------------------------------------------------------------------ ingest
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one Slack ``event_callback`` envelope into SignalEvents."""
        if raw.get("type") != "event_callback":
            return []

        event_id = raw.get("event_id")
        if event_id and event_id in self._seen_event_ids:
            logger.debug("slack duplicate event_id dropped: %s", event_id)
            return []
        if event_id:
            self._seen_event_ids.add(event_id)

        event = raw.get("event", {}) or {}
        mapper = MAPPERS.get(event.get("type"))
        if mapper is None:
            logger.debug("slack event type ignored: %s", event.get("type"))
            return []

        events = [mapper(event, raw, self._tenant_id)]
        await self._enrich(events)
        return events

    # --------------------------------------------------------------- enrichment
    def _ensure_client(self) -> Any:
        """Build the Web-API client + user cache lazily from the bot token."""
        if self._client is None and self._token:
            try:
                from connectors.common import StructuredLogger

                from .client import SlackClient
                from .user_cache import UserCache
                log = StructuredLogger(console_output=False)
                self._client = SlackClient(self._token, log)
                self._users = UserCache(self._client, log)
            except Exception as exc:  # missing dep / bad token — enrichment is best-effort
                logger.warning("slack enrichment disabled: %s", exc)
                self._client = None
        elif self._client is not None and self._users is None:
            try:
                from connectors.common import StructuredLogger

                from .user_cache import UserCache
                self._users = UserCache(self._client, StructuredLogger(console_output=False))
            except Exception:
                self._users = None
        return self._client

    async def _enrich(self, events: list[SignalEvent]) -> None:
        if not (self._token or self._client):
            return
        for ev in events:
            d = ev.data
            cid = d.get("channel")
            if cid and "channel_name" not in d:
                name = await self._cached(self._channel_names, cid, self._channel_name_sync)
                if name:
                    d["channel_name"] = name
            uid = d.get("user")
            if uid and "user_name" not in d:
                uname = await self._cached(self._user_names, uid, self._user_name_sync)
                if uname:
                    d["user_name"] = uname

    async def _cached(self, cache: dict[str, str], key: str, fn) -> str:
        if key in cache:
            return cache[key]
        val = await asyncio.to_thread(fn, key)   # sync HTTP off the event loop
        cache[key] = val or ""                   # cache misses too (don't re-hit the API)
        return cache[key]

    def _channel_name_sync(self, channel_id: str) -> str:
        client = self._ensure_client()
        if client is None:
            return ""
        try:
            info = client.get_channel_info(channel_id)
            return f"#{info.name}" if info and getattr(info, "name", "") else ""
        except Exception:
            return ""

    def _user_name_sync(self, user_id: str) -> str:
        self._ensure_client()
        if self._users is None:
            return ""
        try:
            user = self._users.get_user(user_id)
            return getattr(user, "name", "") or ""
        except Exception:
            return ""
