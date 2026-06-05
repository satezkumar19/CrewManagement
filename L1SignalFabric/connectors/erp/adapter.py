"""ERP fetch adapters — the swap-only-this half of the ERP connector.

A ``FetchAdapter`` knows HOW to read raw change rows from an ERP source since a
watermark. The connector + mappers stay identical whether the rows come from a
mimic, a transactional ``signal_outbox`` table polled over Postgres, or a logical
replication / Debezium feed in prod — only the adapter changes.

``InMemoryOutboxAdapter`` is the Day-1 mimic: an in-process ordered log of change
rows, exactly the shape a DB ``signal_outbox`` (populated by triggers) would
produce.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class RawRecord:
    """One raw ERP change row plus the cursor to persist once processed."""

    cursor: int          # monotonic outbox sequence
    data: dict[str, Any]  # {seq, table, op, occurred_at, data:{...}}


class ErpFetchAdapter(ABC):
    """How to read change rows from an ERP source. Swap this to go live."""

    @abstractmethod
    def start_cursor(self) -> int:
        """The cursor meaning 'from the very beginning'."""

    @abstractmethod
    def fetch(self, since: int, limit: int | None = None) -> List[RawRecord]:
        """Return change rows strictly after ``since``, in sequence order."""


@dataclass
class InMemoryOutboxAdapter(ErpFetchAdapter):
    """Mimic outbox: an ordered in-memory change log (stands in for the DB
    ``signal_outbox`` table). Drive it from a seed list or ``append`` at runtime
    to simulate ERP writes during a demo."""

    rows: List[dict[str, Any]] = field(default_factory=list)

    def start_cursor(self) -> int:
        return 0

    def append(self, *, table: str, op: str, occurred_at: str, data: dict[str, Any]) -> int:
        seq = (self.rows[-1]["seq"] + 1) if self.rows else 1
        self.rows.append(
            {"seq": seq, "table": table, "op": op, "occurred_at": occurred_at, "data": data}
        )
        return seq

    def fetch(self, since: int, limit: int | None = None) -> List[RawRecord]:
        out = [RawRecord(cursor=r["seq"], data=r) for r in self.rows if r["seq"] > since]
        if limit is not None:
            out = out[:limit]
        return out
