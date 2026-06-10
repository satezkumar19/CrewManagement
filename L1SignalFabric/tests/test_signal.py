"""SignalEvent contract: tz-awareness, defaults, stable dedup identity."""

from datetime import datetime, timezone

import pytest

from core.dedup import dedup_key
from core.signal import Lineage, Operation, SignalEvent, SourceSystem


def _event(seq: int = 1) -> SignalEvent:
    return SignalEvent(
        entity="crew",
        key={"crew_id": "C-1"},
        source_system=SourceSystem.CREW_DB,
        tenant_id="t",
        timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
        lineage=Lineage(extraction_id="erp-crew-1", source_sequence=seq),
    )


def test_defaults_delta_and_event_id():
    ev = _event()
    assert ev.operation == Operation.DELTA
    assert ev.event_id  # auto uuid
    assert ev.extracted_at.tzinfo is not None


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        SignalEvent(
            entity="crew", key={}, source_system=SourceSystem.CREW_DB, tenant_id="t",
            timestamp=datetime(2026, 6, 8),  # naive -> rejected
        )


def test_dedup_id_stable_and_distinguishing():
    # same identity -> same dedup_id even though event_id/extracted_at differ
    assert _event(1).dedup_id == _event(1).dedup_id
    # different source_sequence -> different identity
    assert _event(1).dedup_id != _event(2).dedup_id


def test_dedup_key_helper_stable():
    payload = {"crew_id": "C-1", "status": "onboard"}
    a = dedup_key(source="CREW_DB", entity="crew", occurred_at="2026-06-08T00:00:00Z",
                  payload=payload, natural_keys=["crew_id"])
    b = dedup_key(source="CREW_DB", entity="crew", occurred_at="2026-06-08T00:00:00Z",
                  payload={"crew_id": "C-1", "status": "DIFFERENT"}, natural_keys=["crew_id"])
    assert a == b  # natural key only -> status change doesn't alter identity
