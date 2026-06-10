"""Structured JSON logger — shared by every real connector.

Ported from the upstream Slack/Notion scrapers' ``logger.py`` (identical
behaviour, one copy). Writes one JSON object per line to an optional log file and
prints a coloured, human-readable line to stderr. Connectors use this for their
backfill/scrape mode (``cli.py``); the live FastAPI path keeps using the stdlib
``logging`` module.

Log entry shape::

    {"ts": "2026-06-08T14:30:52Z", "level": "INFO", "msg": "...", "k": "v"}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TextIO

# numeric severities (match the scrapers): DEBUG < INFO < WARN < ERROR
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}

# ANSI colours for the stderr console line
_COLORS = {"DEBUG": "\033[36m", "INFO": "\033[32m", "WARN": "\033[33m",
           "WARNING": "\033[33m", "ERROR": "\033[31m"}
_RESET = "\033[0m"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StructuredLogger:
    """File + console structured logger with level filtering."""

    def __init__(
        self,
        log_file: Optional[Path] = None,
        console_output: bool = True,
        min_level: str = "INFO",
    ) -> None:
        self.min_level = _LEVELS.get(min_level.upper(), 20)
        self.console_output = console_output
        self._fh: Optional[TextIO] = None
        if log_file is not None:
            log_file = Path(log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            self._fh = log_file.open("w", encoding="utf-8")

    # --- internals ---
    def _should_log(self, level: str) -> bool:
        return _LEVELS.get(level, 20) >= self.min_level

    def _log(self, level: str, msg: str, **kwargs: Any) -> None:
        if not self._should_log(level):
            return
        entry = {"ts": _now(), "level": level, "msg": msg, **kwargs}
        if self._fh is not None:
            self._fh.write(json.dumps(entry, default=str) + "\n")
            self._fh.flush()
        if self.console_output:
            extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
            color = _COLORS.get(level, "")
            sys.stderr.write(f"{color}[{level}]{_RESET} {msg}"
                             + (f" {extra}" if extra else "") + "\n")

    # --- public API ---
    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log("DEBUG", msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log("INFO", msg, **kwargs)

    def warn(self, msg: str, **kwargs: Any) -> None:
        self._log("WARN", msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log("WARN", msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log("ERROR", msg, **kwargs)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "StructuredLogger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
