"""Database connector: in-memory mimic + real SQLAlchemy (sqlite) adapters."""

import asyncio

from connectors.database import (
    DatabaseConnector,
    InMemoryOutboxAdapter,
    OutboxAdapter,
    UpdatedAtAdapter,
)
from core.signal import Operation, SourceSystem
from core.watermark import InMemoryWatermarkStore


def test_inmemory_outbox_poll_and_resume():
    a = InMemoryOutboxAdapter(key_field="crew_id")
    a.append(table="crew", op="INSERT", occurred_at="2024-06-01T10:00:00Z",
             row={"crew_id": "C1", "name": "Ada"})
    a.append(table="crew", op="UPDATE", occurred_at="2024-06-01T10:05:00Z",
             row={"crew_id": "C2", "name": "Bo"})
    wm = InMemoryWatermarkStore()
    c = DatabaseConnector(tenant_id="t", adapter=a, watermarks=wm)
    sigs = asyncio.run(c.poll())
    assert [s.key["crew_id"] for s in sigs] == ["C1", "C2"]
    assert all(s.source_system == SourceSystem.DATABASE for s in sigs)
    assert all(s.operation == Operation.DELTA for s in sigs)
    assert c.position() == 2
    # resume: a fresh connector reading the same watermark store emits nothing
    c2 = DatabaseConnector(tenant_id="t", adapter=a, watermarks=wm)
    assert asyncio.run(c2.poll()) == []


def test_dedup_id_stable_across_repoll():
    a = InMemoryOutboxAdapter(key_field="id")
    a.append(table="crew", op="INSERT", occurred_at="2024-06-01T10:00:00Z",
             row={"id": "C1"})
    c1 = DatabaseConnector(tenant_id="t", adapter=a)
    c2 = DatabaseConnector(tenant_id="t", adapter=a)
    s1 = asyncio.run(c1.poll())[0]
    s2 = asyncio.run(c2.poll())[0]
    assert s1.dedup_id == s2.dedup_id  # at-least-once is safe


def _sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path}/db.sqlite"


def test_real_outbox_adapter_sqlite(tmp_path):
    from sqlalchemy import create_engine, text
    url = _sqlite_url(tmp_path)
    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE signal_outbox (seq INTEGER PRIMARY KEY, "
                          "table_name TEXT, op TEXT, occurred_at TEXT, payload TEXT)"))
        conn.execute(text("INSERT INTO signal_outbox VALUES "
                          "(1,'crew','INSERT','2024-06-01T10:00:00Z','{\"id\": \"C1\"}')"))
        conn.execute(text("INSERT INTO signal_outbox VALUES "
                          "(2,'vessel','UPDATE','2024-06-01T11:00:00Z','{\"id\": \"V9\"}')"))
    c = DatabaseConnector(tenant_id="t", adapter=OutboxAdapter(url=url))
    sigs = asyncio.run(c.poll())
    assert [s.entity for s in sigs] == ["crew", "vessel"]
    assert sigs[0].key == {"id": "C1"} and sigs[0].metadata["op"] == "INSERT"
    assert c.position() == 2


def test_real_updated_at_adapter_sqlite(tmp_path):
    from sqlalchemy import create_engine, text
    url = _sqlite_url(tmp_path)
    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE crew (id TEXT PRIMARY KEY, name TEXT, updated_at TEXT)"))
        conn.execute(text("INSERT INTO crew VALUES ('C1','Ada','2024-06-01T10:00:00')"))
        conn.execute(text("INSERT INTO crew VALUES ('C2','Bo','2024-06-02T10:00:00')"))
    adapter = UpdatedAtAdapter(url=url, table="crew", entity="crew")
    c = DatabaseConnector(tenant_id="t", adapter=adapter)
    sigs = asyncio.run(c.poll())
    assert {s.key["id"] for s in sigs} == {"C1", "C2"}
    assert sigs[-1].data["name"] == "Bo"
    # watermark now at the latest updated_at → no new rows
    assert asyncio.run(c.poll()) == []
