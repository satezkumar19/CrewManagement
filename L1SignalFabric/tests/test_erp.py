"""ERP connector skeleton: outbox mapping, watermark advance, lossless resume."""

import asyncio

from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from core.signal import Operation, SourceSystem
from core.watermark import FileWatermarkStore


def _seed(outbox: InMemoryOutboxAdapter) -> None:
    outbox.append(table="crew", op="update", occurred_at="2026-06-08T09:00:00Z",
                  data={"crew_id": "C-1", "rank": "2/O", "status": "onboard"})
    outbox.append(table="contract", op="insert", occurred_at="2026-06-08T09:01:00Z",
                  data={"contract_id": "K-9", "crew_id": "C-1", "state": "active"})
    outbox.append(table="vessel_port", op="update", occurred_at="2026-06-08T09:02:00Z",
                  data={"vessel_id": "V-7", "port": "Rotterdam", "status": "berthed"})


def test_outbox_maps_to_three_source_systems():
    outbox = InMemoryOutboxAdapter()
    _seed(outbox)
    erp = ErpConnector(tenant_id="t", adapter=outbox)

    signals = asyncio.run(erp.poll())
    assert len(signals) == 3
    by_system = {s.source_system: s for s in signals}
    assert by_system[SourceSystem.CREW_DB].entity == "crew"
    assert by_system[SourceSystem.CREW_DB].key == {"crew_id": "C-1"}
    assert by_system[SourceSystem.CONTRACT_CLM].key == {"contract_id": "K-9"}
    assert by_system[SourceSystem.VESSEL_PORT_DB].key == {"vessel_id": "V-7"}
    assert all(s.operation == Operation.DELTA for s in signals)
    # lineage carries the outbox sequence (used for dedup / resume)
    assert by_system[SourceSystem.CREW_DB].lineage.source_sequence == 1


def test_watermark_advances_and_no_redelivery():
    outbox = InMemoryOutboxAdapter()
    _seed(outbox)
    erp = ErpConnector(tenant_id="t", adapter=outbox)

    first = asyncio.run(erp.poll())
    assert len(first) == 3
    assert erp.position() == 3
    # nothing new -> empty second poll (cursor advanced past all rows)
    assert asyncio.run(erp.poll()) == []

    # a new ERP write shows up on the next poll only
    outbox.append(table="crew", op="update", occurred_at="2026-06-08T10:00:00Z",
                  data={"crew_id": "C-2", "status": "sign_off"})
    third = asyncio.run(erp.poll())
    assert [s.key for s in third] == [{"crew_id": "C-2"}]


def test_lossless_resume_via_file_watermark(tmp_path):
    wm_path = str(tmp_path / "wm.json")
    outbox = InMemoryOutboxAdapter()
    _seed(outbox)

    # connector A consumes everything, persisting its cursor to disk
    erp_a = ErpConnector(tenant_id="t", adapter=outbox, watermarks=FileWatermarkStore(wm_path))
    assert len(asyncio.run(erp_a.poll())) == 3

    # a fresh connector (simulating a restart) resumes from the persisted cursor:
    # 0 re-delivered, 0 lost
    erp_b = ErpConnector(tenant_id="t", adapter=outbox, watermarks=FileWatermarkStore(wm_path))
    assert erp_b.position() == 3
    assert asyncio.run(erp_b.poll()) == []
