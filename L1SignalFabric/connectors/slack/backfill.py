"""Slack Web-API backfill connector — the streaming realization of the scraper.

The upstream Slack *scraper* pulled channel history + threads in a batch and wrote
JSONL. This brings that whole capability into L1 as a watermark-checkpointed
**pull** connector:

  * ``poll()`` — fetch messages newer than the stored watermark across the
    member channels, normalize them (resolving users + reactions), and emit
    canonical SignalEvents; advance the watermark to the newest ``ts`` seen so a
    restart resumes losslessly.
  * ``scrape()`` — the full batch path (channel resolution → history → threads),
    used by the CLI to (re)produce the ``slack.jsonl`` + manifest + metrics bundle,
    and optionally to publish every event to the bus.

Channel resolution, bot filtering, thread-reply fetching, user-email resolution
and per-run metrics are all preserved from the scraper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional, Union

from connectors.common import (
    OutputWriter,
    RateLimitError,
    ScrapeMetrics,
    StructuredLogger,
)
from connectors.common.poller import PollingConnector
from core.signal import SignalEvent, SourceSystem
from core.watermark import WatermarkStore

from .client import SlackClient
from .mappers import message_model_to_signal
from .models import ChannelInfo, SlackMessage, SlackReaction
from .user_cache import UserCache

EmitFn = Callable[[SignalEvent], Union[None, Awaitable[None]]]


@dataclass
class SlackBackfillConfig:
    channels: Union[str, List[str]] = "all"   # "all" or explicit ids
    since_timestamp: Optional[datetime] = None
    until_timestamp: Optional[datetime] = None
    exclude_thread_replies: bool = False
    exclude_bots: bool = False
    max_replies_per_thread: int = 20
    extra: dict = field(default_factory=dict)


def _is_bot_message(raw: dict) -> bool:
    return (raw.get("subtype") == "bot_message"
            or bool(raw.get("bot_id"))
            or str(raw.get("user", "")).startswith("B"))


def _ts_dt(ts: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


class SlackBackfillConnector(PollingConnector):
    """Pull connector over the Slack Web API (watermark = newest ts seen)."""

    name = "slack-backfill"
    source_system = SourceSystem.SLACK

    def __init__(
        self,
        *,
        tenant_id: str,
        client: SlackClient,
        config: Optional[SlackBackfillConfig] = None,
        logger: Optional[StructuredLogger] = None,
        watermarks: Optional[WatermarkStore] = None,
    ) -> None:
        # cursor is the newest Slack `ts` (string) we have emitted; "0" = genesis
        super().__init__(tenant_id=tenant_id, start_cursor="0", watermarks=watermarks)
        self.client = client
        self.config = config or SlackBackfillConfig()
        self.logger = logger or StructuredLogger(console_output=False)
        self.user_cache = UserCache(client, self.logger)

    # ---- channel resolution ----
    def _resolve_channels(self) -> List[ChannelInfo]:
        if self.config.channels == "all":
            return [c for c in self.client.list_channels("public_channel") if c.is_member]
        out: List[ChannelInfo] = []
        for cid in self.config.channels:
            info = self.client.get_channel_info(cid)
            if info is not None:
                out.append(info)
            else:
                self.logger.warn("channel not resolved", channel=cid)
        return out

    # ---- raw → model ----
    def _convert(self, raw: dict, channel: ChannelInfo) -> Optional[SlackMessage]:
        try:
            user_id = raw.get("user") or raw.get("bot_id") or "unknown"
            user = self.user_cache.get_user(user_id)
            reactions = [SlackReaction(name=r.get("name", ""), users=r.get("users", []),
                                       count=r.get("count", 0))
                         for r in raw.get("reactions", [])]
            return SlackMessage(
                channel_id=channel.id,
                channel=f"#{channel.name}",
                ts=raw.get("ts", ""),
                thread_ts=raw.get("thread_ts"),
                user=user,
                text=raw.get("text", ""),
                timestamp=_ts_dt(raw.get("ts", "")),
                reactions=reactions,
                reply_count=int(raw.get("reply_count", 0) or 0),
            )
        except Exception as exc:  # noqa: BLE001 - one bad message must not abort the run
            self.logger.warn("message convert failed", error=str(exc))
            return None

    # ---- L1 ingest contract ----
    async def ingest(self, raw: dict[str, Any]) -> list[SignalEvent]:
        """Normalize one raw history message (with channel in ``_channel``)."""
        channel = raw.get("_channel")
        if isinstance(channel, dict):
            channel = ChannelInfo(**channel)
        if channel is None:
            channel = ChannelInfo(id=raw.get("channel", ""), name=raw.get("channel", ""))
        msg = self._convert(raw, channel)
        return [message_model_to_signal(msg, self._tenant_id)] if msg else []

    # ---- L1 poll contract ----
    async def poll(self, limit: Optional[int] = None) -> List[SignalEvent]:
        """Emit messages newer than the watermark; advance to the newest ts."""
        oldest = float(self._cursor) if self._cursor and self._cursor != "0" else None
        if self.config.since_timestamp and oldest is None:
            oldest = self.config.since_timestamp.timestamp()
        latest = self.config.until_timestamp.timestamp() if self.config.until_timestamp else None

        out: List[SignalEvent] = []
        max_ts = float(self._cursor or 0)
        for channel in self._resolve_channels():
            for raw in self.client.get_channel_history(channel.id, oldest=oldest, latest=latest):
                if self.config.exclude_bots and _is_bot_message(raw):
                    continue
                msg = self._convert(raw, channel)
                if msg is None:
                    continue
                out.append(message_model_to_signal(msg, self._tenant_id))
                max_ts = max(max_ts, float(raw.get("ts", 0) or 0))
                if limit and len(out) >= limit:
                    break
        if max_ts > float(self._cursor or 0):
            self.commit(f"{max_ts:.6f}")
        if out:
            self.logger.info("slack poll", emitted=len(out), cursor=self._cursor)
        return out

    # ---- full batch scrape (CLI / batch-compatible output) ----
    def scrape(
        self,
        writer: Optional[OutputWriter] = None,
        on_event: Optional[EmitFn] = None,
    ) -> ScrapeMetrics:
        metrics = ScrapeMetrics()
        metrics.extra.update({"channels": {"total": 0, "successful": 0, "failed": 0},
                              "messages": {"with_threads": 0}})
        channels = self._resolve_channels()
        metrics.extra["channels"]["total"] = len(channels)
        oldest = self.config.since_timestamp.timestamp() if self.config.since_timestamp else None
        latest = self.config.until_timestamp.timestamp() if self.config.until_timestamp else None

        if writer is not None:
            writer.open()
        try:
            for channel in channels:
                try:
                    parents = self._scrape_channel(channel, oldest, latest, writer,
                                                   on_event, metrics)
                    metrics.extra["channels"]["successful"] += 1
                    if not self.config.exclude_thread_replies:
                        self._scrape_threads(channel, parents, writer, on_event, metrics)
                except RateLimitError as exc:
                    metrics.add_error(f"rate limit: {exc}")
                    self.logger.error("aborting on rate limit", error=str(exc))
                    break
                except Exception as exc:  # noqa: BLE001
                    metrics.extra["channels"]["failed"] += 1
                    metrics.add_error(f"channel {channel.id}: {exc}")
        finally:
            if writer is not None:
                writer.close()

        metrics.api_calls_total = self.client.api_calls
        metrics.api_rate_limit_hits = self.client.rate_limit_hits
        metrics.extra["users"] = self.user_cache.get_stats()
        metrics.finalize()
        if writer is not None:
            writer.write_manifest(SourceSystem.SLACK.value, metrics.records_total)
            writer.write_metrics(metrics)
        return metrics

    def _emit(self, msg: SlackMessage, writer, on_event, metrics) -> None:
        signal = message_model_to_signal(msg, self._tenant_id)
        metrics.records_total += 1
        metrics.records_successful += 1
        metrics.signals_emitted += 1
        if writer is not None:
            writer.write_event(signal)
        if on_event is not None:
            res = on_event(signal)
            if hasattr(res, "__await__"):  # tolerate async sink in a sync scrape
                import asyncio
                asyncio.get_event_loop().run_until_complete(res)

    def _scrape_channel(self, channel, oldest, latest, writer, on_event, metrics) -> List[dict]:
        parents: List[dict] = []
        for raw in self.client.get_channel_history(channel.id, oldest=oldest, latest=latest):
            if self.config.exclude_bots and _is_bot_message(raw):
                continue
            msg = self._convert(raw, channel)
            if msg is None:
                continue
            self._emit(msg, writer, on_event, metrics)
            if msg.reply_count > 0:
                parents.append(raw)
                metrics.extra["messages"]["with_threads"] += 1
        return parents

    def _scrape_threads(self, channel, parents, writer, on_event, metrics) -> None:
        for parent in parents:
            thread_ts = parent.get("thread_ts") or parent.get("ts")
            try:
                replies = self.client.get_thread_replies(
                    channel.id, thread_ts, limit=self.config.max_replies_per_thread)
            except RateLimitError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warn("thread fetch failed", channel=channel.id, error=str(exc))
                continue
            for raw in replies:
                if self.config.exclude_bots and _is_bot_message(raw):
                    continue
                msg = self._convert(raw, channel)
                if msg is not None:
                    self._emit(msg, writer, on_event, metrics)
