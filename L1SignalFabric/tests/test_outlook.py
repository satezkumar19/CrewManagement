"""Outlook connector: Graph mail mapping, webhook verify, delta poll."""

import asyncio

from connectors.outlook import OutlookConnector
from connectors.outlook.mappers import graph_message_to_record, message_to_signal
from core.connector import InboundRequest
from core.signal import SourceSystem


def _graph_msg(mid="m1", subject="Hello", categories=None):
    return {"id": mid, "internetMessageId": f"<{mid}>", "conversationId": "c1",
            "from": {"emailAddress": {"address": "a@x"}},
            "toRecipients": [{"emailAddress": {"address": "b@y"}}],
            "ccRecipients": [{"emailAddress": {"address": "c@z"}}],
            "subject": subject, "categories": categories or [],
            "receivedDateTime": "2024-06-01T10:00:00Z"}


def test_graph_message_to_record():
    rec = graph_message_to_record(_graph_msg())
    assert rec["from"] == "a@x"
    assert rec["to"] == ["b@y"] and rec["cc"] == ["c@z"]
    assert rec["thread_id"] == "c1"


def test_message_to_signal_source_and_signoff():
    sig = message_to_signal(_graph_msg(categories=["crew/sign-off"]), "t")
    assert sig.source_system == SourceSystem.OUTLOOK
    assert sig.metadata.get("l2Intent") == "CREATE_SIGNOFF_EVENT"


def test_verify_handshake_and_client_state():
    c = OutlookConnector(tenant_id="t", client_state="kept")
    # handshake echoes validationToken
    vr = c.verify(InboundRequest(query={"validationToken": "tok"}))
    assert vr.outcome.value == "challenge" and vr.challenge == "tok"
    # good clientState
    ok = c.verify(InboundRequest(json={"value": [{"clientState": "kept"}]}))
    assert ok.outcome.value == "ok"
    # bad clientState
    bad = c.verify(InboundRequest(json={"value": [{"clientState": "nope"}]}))
    assert bad.outcome.value == "reject"


def test_ingest_notification_with_inline_message():
    c = OutlookConnector(tenant_id="t")
    body = {"value": [{"subscriptionId": "s1", "resourceData": {"id": "m1"},
                       "_message": _graph_msg()}]}
    sigs = asyncio.run(c.ingest(body))
    assert len(sigs) == 1 and sigs[0].key == {"message_id": "<m1>"}
    # duplicate resourceData id dropped
    assert asyncio.run(c.ingest(body)) == []


def test_delta_poll_advances_watermark():
    class FakeOutlook:
        api_calls = 0
        rate_limit_hits = 0

        def delta(self, folder="inbox", start=None, select=""):
            if start:
                return [], "link2"
            return [_graph_msg("m1"), {"id": "m2", "@removed": {"reason": "deleted"}}], "link2"

    c = OutlookConnector(tenant_id="t", client=FakeOutlook())
    sigs = asyncio.run(c.poll())
    assert len(sigs) == 1                  # removed item skipped
    assert c.position() == "link2"
    assert asyncio.run(c.poll()) == []     # delta link returns nothing new
