"""Conduit-compatible output writer (JSONL + manifest.json + metrics.json).

Unifies the Slack and Notion scrapers' ``writer.py``. A connector's *backfill*
mode (``cli.py scrape``) writes the canonical :class:`~core.signal.SignalEvent`
stream to ``<source>.jsonl`` plus a v2.0 ``manifest.json`` and a ``metrics.json``,
so a backfill produces the exact artifacts the upstream Conduit file path expects
— the L1 stream and the Conduit batch stay wire-compatible.

Unlike the scrapers (which wrote their bespoke per-source row shape), this writes
the normalized SignalEvent dict, so backfill output is identical to what flows
over the live bus.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TextIO

from core.signal import SignalEvent

from .logger import StructuredLogger
from .metrics import ScrapeMetrics


def _utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


class OutputWriter:
    """Writes a backfill run's SignalEvents + manifest + metrics to a directory."""

    def __init__(
        self,
        output_dir: str,
        *,
        source: str,
        entity: str,
        logger: Optional[StructuredLogger] = None,
        extraction_id: Optional[str] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.source = source                # e.g. "slack", "notion"
        self.entity = entity                # e.g. "messages", "pages"
        self.logger = logger or StructuredLogger(console_output=False)
        self.extraction_id = extraction_id or f"{source}-scrape-{_utcstamp()}"

        self.jsonl_path = self.output_dir / f"{source}.jsonl"
        self.manifest_path = self.output_dir / "manifest.json"
        self.metrics_path = self.output_dir / "metrics.json"
        self._fh: Optional[TextIO] = None
        self._count = 0

    def open(self) -> None:
        self._fh = self.jsonl_path.open("w", encoding="utf-8")
        self._count = 0

    def write_event(self, event: SignalEvent) -> None:
        assert self._fh is not None, "writer not opened"
        self._fh.write(event.model_dump_json() + "\n")
        self._count += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def count(self) -> int:
        return self._count

    def write_manifest(self, source_system: str, record_count: Optional[int] = None) -> None:
        n = self._count if record_count is None else record_count
        manifest = {
            "version": "2.0",
            "extractionId": self.extraction_id,
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "description": f"{self.source} {self.entity} backfill ({n} records)",
            "files": [
                {
                    "path": self.jsonl_path.name,
                    "type": "UNSTRUCTURED",
                    "entity": self.entity,
                    "sourceSystem": source_system,
                    "format": "JSONL",
                    "encoding": "UTF-8",
                    "description": f"{self.source} {self.entity} ({n} records)",
                }
            ],
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def write_metrics(self, metrics: ScrapeMetrics) -> None:
        metrics.output_file = str(self.jsonl_path)
        if self.jsonl_path.exists():
            metrics.output_size_bytes = self.jsonl_path.stat().st_size
        self.metrics_path.write_text(json.dumps(metrics.to_dict(), indent=2),
                                     encoding="utf-8")

    def __enter__(self) -> "OutputWriter":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
