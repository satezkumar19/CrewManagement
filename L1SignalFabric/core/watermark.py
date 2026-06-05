"""Watermark / checkpoint stores for pull (CDC/outbox) connectors.

A watermark is whatever a source uses to mark progress — an autoincrement seq
for an outbox feed, an ISO timestamp for a manifest. Persisting it after the sink
acks is what makes restart lossless (the ERP exit criterion: 50 records, 0 loss).

``InMemoryWatermarkStore`` is for tests; ``FileWatermarkStore`` persists across
process restarts. A Redis/Postgres-backed store drops in behind the same ABC on
Day 4.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

Cursor = Union[int, str]


class WatermarkStore(ABC):
    @abstractmethod
    def get(self, name: str, default: Cursor) -> Cursor: ...

    @abstractmethod
    def set(self, name: str, cursor: Cursor) -> None: ...


class InMemoryWatermarkStore(WatermarkStore):
    def __init__(self) -> None:
        self._d: dict[str, Cursor] = {}

    def get(self, name: str, default: Cursor) -> Cursor:
        return self._d.get(name, default)

    def set(self, name: str, cursor: Cursor) -> None:
        self._d[name] = cursor


class FileWatermarkStore(WatermarkStore):
    """Persists cursors to a JSON file so polling resumes across runs."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._d: dict[str, Cursor] = {}
        if self._path.exists():
            self._d = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, name: str, default: Cursor) -> Cursor:
        return self._d.get(name, default)

    def set(self, name: str, cursor: Cursor) -> None:
        self._d[name] = cursor
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._d, indent=2), encoding="utf-8")
