"""Slack domain models — ported from the upstream Slack scraper.

These mirror the scraper's ``models.py`` field-for-field so the backfill path
captures exactly what the batch scraper did (users with resolved e-mail,
reactions, thread reply counts, channel membership). The live Events-API path
maps straight to :class:`~core.signal.SignalEvent`; these richer models back the
Web-API backfill/scrape mode.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SlackUser(BaseModel):
    user_id: str
    name: str
    email: Optional[str] = None


class SlackReaction(BaseModel):
    name: str
    users: List[str] = Field(default_factory=list)
    count: int = 0


class SlackMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_id: str
    channel: str                       # "#general"
    ts: str                            # Slack ts ("1719980964.000100")
    thread_ts: Optional[str] = None
    user: SlackUser
    text: str = ""
    timestamp: datetime
    reactions: List[SlackReaction] = Field(default_factory=list)
    reply_count: int = 0

    def to_jsonl_dict(self) -> dict:
        ts = self.timestamp
        ts_str = ts.isoformat() + "Z" if ts.tzinfo is None else ts.isoformat()
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "channel": self.channel,
            "ts": self.ts,
            "thread_ts": self.thread_ts,
            "user": {"user_id": self.user.user_id, "name": self.user.name,
                     "email": self.user.email},
            "text": self.text,
            "timestamp": ts_str,
            "reactions": [r.model_dump() for r in self.reactions],
            "reply_count": self.reply_count,
        }


class ChannelInfo(BaseModel):
    id: str
    name: str
    is_member: bool = False
    is_private: bool = False
    num_members: int = 0
