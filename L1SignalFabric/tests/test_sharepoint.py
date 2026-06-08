"""SharePoint connector: drive/list mapping, delta poll, per-target watermarks."""

import asyncio

from connectors.sharepoint import (
    DriveTarget,
    ListTarget,
    SharePointConnector,
    drive_item_to_signal,
    list_item_to_signal,
)
from core.connector import InboundRequest
from core.signal import SourceSystem


def _drive_item(iid="i1", removed=False):
    item = {"id": iid, "name": "Crew.xlsx", "webUrl": "http://sp/i1", "size": 10,
            "file": {"mimeType": "app/xlsx"},
            "parentReference": {"driveId": "d1", "path": "/drive/root:/Crew"},
            "lastModifiedDateTime": "2024-06-01T10:00:00Z",
            "createdBy": {"user": {"id": "u1", "displayName": "Al"}}}
    if removed:
        item["@removed"] = {"state": "deleted"}
    return item


def test_drive_item_mapping():
    sig = drive_item_to_signal(_drive_item(), "t", "d1")
    assert sig.entity == "drive_item"
    assert sig.key == {"drive_id": "d1", "item_id": "i1"}
    assert sig.data["kind"] == "file" and sig.data["name"] == "Crew.xlsx"
    assert sig.source_system == SourceSystem.SHAREPOINT


def test_list_item_mapping():
    sig = list_item_to_signal({"id": "L1", "fields": {"Title": "V7", "Status": "Open"},
                               "lastModifiedDateTime": "2024-06-02T00:00:00Z"},
                              "t", "site1", "list1")
    assert sig.entity == "list_item"
    assert sig.data["fields"]["Title"] == "V7"


def test_verify_handshake():
    c = SharePointConnector(tenant_id="t")
    vr = c.verify(InboundRequest(query={"validationToken": "v"}))
    assert vr.outcome.value == "challenge" and vr.challenge == "v"


def test_delta_poll_per_target_watermark():
    class FakeSP:
        api_calls = 0
        rate_limit_hits = 0

        def drive_delta(self, drive_id, start=None):
            if start:
                return [], "dlink2"
            return [_drive_item("i1"), {"root": {}, "id": "root"}], "dlink2"

        def list_delta(self, site_id, list_id, start=None):
            if start:
                return [], "llink2"
            return [{"id": "L1", "fields": {"Title": "V7"},
                     "lastModifiedDateTime": "2024-06-02T00:00:00Z"}], "llink2"

    c = SharePointConnector(tenant_id="t", client=FakeSP(),
                            targets=[DriveTarget("d1"), ListTarget("site1", "list1")])
    sigs = asyncio.run(c.poll())
    entities = sorted(s.entity for s in sigs)
    assert entities == ["drive_item", "list_item"]   # root anchor skipped
    snap = c.position()
    assert snap["sharepoint:drive:d1"] == "dlink2"
    assert snap["sharepoint:list:site1:list1"] == "llink2"
    assert asyncio.run(c.poll()) == []               # both delta links drained
