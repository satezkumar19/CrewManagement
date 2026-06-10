"""SharePoint connector: folder-item mapping, webhook verify, folder poll + dedupe."""

import asyncio

from connectors.sharepoint import (
    SharePointConnector,
    folder_item_to_signal,
)
from core.connector import InboundRequest
from core.signal import SourceSystem


def _item(iid="i1", name="Crew.xlsx", is_folder=False):
    return {"id": iid, "name": name, "size": 10,
            "modified": "2024-06-01T10:00:00Z", "is_folder": is_folder,
            "mime_type": None if is_folder else "app/xlsx",
            "web_url": f"http://sp/{iid}"}


class FakeSP:
    """Minimal stand-in for SharePointClient — list_folder over a fixed map."""

    hostname = "contoso.sharepoint.com"
    site_path = "/sites/Crew"

    def __init__(self, by_folder):
        self.by_folder = by_folder
        self.calls = 0

    def list_folder(self, folder):
        self.calls += 1
        return list(self.by_folder.get(folder, []))


def test_folder_item_mapping():
    sig = folder_item_to_signal(_item(), "t", hostname="contoso.sharepoint.com",
                                site_path="/sites/Crew", folder_path="Shared Documents/crew")
    assert sig.entity == "drive_item"
    assert sig.key == {"site": "contoso.sharepoint.com/sites/Crew", "item_id": "i1"}
    assert sig.data["kind"] == "file" and sig.data["name"] == "Crew.xlsx"
    assert sig.data["path"] == "Shared Documents/crew"
    assert sig.source_system == SourceSystem.SHAREPOINT


def test_folder_item_mapping_folder_kind():
    sig = folder_item_to_signal(_item("d1", "sub", is_folder=True), "t")
    assert sig.data["kind"] == "folder"


def test_verify_handshake():
    c = SharePointConnector(tenant_id="t")
    vr = c.verify(InboundRequest(query={"validationToken": "v"}))
    assert vr.outcome.value == "challenge" and vr.challenge == "v"


def test_ingest_inline_item():
    c = SharePointConnector(tenant_id="t")
    sigs = asyncio.run(c.ingest(_item()))
    assert len(sigs) == 1 and sigs[0].entity == "drive_item"


def test_folder_poll_across_folders_and_dedupes():
    fake = FakeSP({
        "Shared Documents/crew": [_item("i1"), _item("d1", "2024", is_folder=True)],
        "Shared Documents/ops":  [_item("i2", "Ops.docx")],
    })
    c = SharePointConnector(tenant_id="t", client=fake,
                            folder_paths=["Shared Documents/crew", "Shared Documents/ops"])
    sigs = asyncio.run(c.poll())
    assert sorted(s.key["item_id"] for s in sigs) == ["d1", "i1", "i2"]
    assert all(s.entity == "drive_item" for s in sigs)
    # re-listing the same contents emits nothing new (seen-set dedupe)
    assert asyncio.run(c.poll()) == []


def test_poll_no_client_is_noop():
    assert asyncio.run(SharePointConnector(tenant_id="t").poll()) == []
