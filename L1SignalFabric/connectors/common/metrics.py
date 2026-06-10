"""Per-run scrape/backfill metrics — shared dataclass.

Unifies the ``ScrapeMetrics`` models the Slack and Notion scrapers each defined.
The common counters live here; connectors stash source-specific counts in the
free-form :attr:`extra` dict so nothing from the originals is lost (e.g. Slack's
``channels_*`` / ``users_*``, Notion's ``databases_*`` / ``blocks_fetched``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@dataclass
class ScrapeMetrics:
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    # entity counters (records = messages / pages / emails / rows …)
    records_total: int = 0
    records_successful: int = 0
    records_failed: int = 0

    # signal pipeline counters
    signals_emitted: int = 0

    # API counters (populated from the client)
    api_calls_total: int = 0
    api_rate_limit_hits: int = 0

    errors: List[str] = field(default_factory=list)
    output_file: str = ""
    output_size_bytes: int = 0

    #: source-specific counters (channels, databases, blocks, threads, …)
    extra: Dict[str, Any] = field(default_factory=dict)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def finalize(self) -> "ScrapeMetrics":
        self.completed_at = _utcnow()
        self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "duration_seconds": round(self.duration_seconds, 3),
            "records": {
                "total": self.records_total,
                "successful": self.records_successful,
                "failed": self.records_failed,
            },
            "signals_emitted": self.signals_emitted,
            "api_calls": {
                "total": self.api_calls_total,
                "rate_limit_hits": self.api_rate_limit_hits,
            },
            "errors": self.errors,
            "output_file": self.output_file,
            "output_size_bytes": self.output_size_bytes,
            **self.extra,
        }
