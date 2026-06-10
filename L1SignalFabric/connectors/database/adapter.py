"""Database fetch adapters — the swap-only-this half of the DB connector.

Generalizes the ERP connector's outbox pattern to any SQL source via SQLAlchemy
(``psycopg2``/``sqlite``/… — whatever the DB URL drives). Three adapters, one
``RawRecord`` envelope so the mapper and connector are identical regardless of
CDC strategy:

  * :class:`OutboxAdapter` — polls a transactional ``signal_outbox`` table
    (monotonic ``seq``, ``table``, ``op``, ``occurred_at``, JSON ``payload``),
    exactly the shape DB triggers / Debezium produce. Cursor = last seq.
  * :class:`UpdatedAtAdapter` — polls a business table by an ``updated_at``
    high-watermark (no outbox required). Cursor = last ISO timestamp.
  * :class:`InMemoryOutboxAdapter` — the Day-1 mimic (no DB) for tests/demo.

``sqlalchemy`` is imported lazily, so the package imports without it.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

Cursor = Union[int, str]


@dataclass
class RawRecord:
    """One change row + the cursor to persist once its signals are sinked.

    ``data`` envelope: ``{table, op, occurred_at, key:{...}, row:{...}}``.
    """

    cursor: Cursor
    data: Dict[str, Any]


class SqlFetchAdapter(ABC):
    """How to read change rows from a SQL source since a watermark."""

    @abstractmethod
    def start_cursor(self) -> Cursor: ...

    @abstractmethod
    def fetch(self, since: Cursor, limit: Optional[int] = None) -> List[RawRecord]: ...


def _engine(url: str):
    from sqlalchemy import create_engine  # lazy
    return create_engine(url, future=True)


@dataclass
class OutboxAdapter(SqlFetchAdapter):
    """Polls a transactional outbox table over SQLAlchemy."""

    url: str
    table: str = "signal_outbox"
    seq_col: str = "seq"
    table_col: str = "table_name"
    op_col: str = "op"
    occurred_col: str = "occurred_at"
    payload_col: str = "payload"
    key_field: str = "id"            # pk field name inside the JSON payload

    def __post_init__(self) -> None:
        self._eng = _engine(self.url)

    def start_cursor(self) -> int:
        return 0

    def fetch(self, since: Cursor, limit: Optional[int] = None) -> List[RawRecord]:
        from sqlalchemy import text  # lazy
        sql = (f"SELECT {self.seq_col}, {self.table_col}, {self.op_col}, "
               f"{self.occurred_col}, {self.payload_col} FROM {self.table} "
               f"WHERE {self.seq_col} > :since ORDER BY {self.seq_col} ASC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        out: List[RawRecord] = []
        with self._eng.connect() as conn:
            for row in conn.execute(text(sql), {"since": int(since)}):
                seq, table, op, occurred, payload = row
                payload = payload if isinstance(payload, dict) else json.loads(payload or "{}")
                occ = occurred.isoformat() if hasattr(occurred, "isoformat") else str(occurred)
                out.append(RawRecord(cursor=int(seq), data={
                    "table": table, "op": op, "occurred_at": occ,
                    "key": {self.key_field: payload.get(self.key_field)},
                    "row": payload,
                }))
        return out


@dataclass
class UpdatedAtAdapter(SqlFetchAdapter):
    """Polls a business table by an ``updated_at`` high-watermark."""

    url: str
    table: str
    entity: str
    key_col: str = "id"
    updated_col: str = "updated_at"

    def __post_init__(self) -> None:
        self._eng = _engine(self.url)

    def start_cursor(self) -> str:
        return "1970-01-01T00:00:00"

    def fetch(self, since: Cursor, limit: Optional[int] = None) -> List[RawRecord]:
        from sqlalchemy import text  # lazy
        sql = (f"SELECT * FROM {self.table} WHERE {self.updated_col} > :since "
               f"ORDER BY {self.updated_col} ASC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        out: List[RawRecord] = []
        with self._eng.connect() as conn:
            for row in conn.execute(text(sql), {"since": str(since)}):
                d = dict(row._mapping)
                updated = d.get(self.updated_col)
                cur = updated.isoformat() if hasattr(updated, "isoformat") else str(updated)
                out.append(RawRecord(cursor=cur, data={
                    "table": self.entity, "op": "UPDATE", "occurred_at": cur,
                    "key": {self.key_col: d.get(self.key_col)},
                    "row": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                            for k, v in d.items()},
                }))
        return out


@dataclass
class InMemoryOutboxAdapter(SqlFetchAdapter):
    """No-DB mimic outbox (ordered change log) for tests & the Day-1 demo."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    key_field: str = "id"

    def start_cursor(self) -> int:
        return 0

    def append(self, *, table: str, op: str, occurred_at: str, row: Dict[str, Any]) -> int:
        seq = (self.rows[-1]["seq"] + 1) if self.rows else 1
        self.rows.append({"seq": seq, "table": table, "op": op,
                          "occurred_at": occurred_at, "row": row})
        return seq

    def fetch(self, since: Cursor, limit: Optional[int] = None) -> List[RawRecord]:
        out = [
            RawRecord(cursor=r["seq"], data={
                "table": r["table"], "op": r["op"], "occurred_at": r["occurred_at"],
                "key": {self.key_field: r["row"].get(self.key_field)},
                "row": r["row"],
            })
            for r in self.rows if r["seq"] > int(since)
        ]
        if limit is not None:
            out = out[:limit]
        return out
