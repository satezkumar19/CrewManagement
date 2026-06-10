"""Outlook connector: Graph mail mapping, webhook verify, unread poll + mark-read."""

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


class FakeOutlook:
    """Minimal stand-in for OutlookClient — unread list + mark-read."""

    def __init__(self, unread):
        self.unread = list(unread)   # newest-first, like Graph $orderby desc
        self.marked: list[str] = []

    def list_unread(self, top=50):
        return [m for m in self.unread if m["id"] not in self.marked][:top]

    def mark_read(self, mid):
        self.marked.append(mid)


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


def test_ingest_graph_message_resource():
    c = OutlookConnector(tenant_id="t")
    sigs = asyncio.run(c.ingest(_graph_msg()))
    assert len(sigs) == 1 and sigs[0].key == {"message_id": "<m1>"}


def test_ingest_notification_triggers_poll():
    # A Graph change-notification with a live client kicks an unread poll.
    c = OutlookConnector(tenant_id="t", client=FakeOutlook([_graph_msg("m1")]))
    body = {"value": [{"subscriptionId": "s1", "resourceData": {"id": "m1"}}]}
    sigs = asyncio.run(c.ingest(body))
    assert len(sigs) == 1 and sigs[0].key == {"message_id": "<m1>"}


def test_unread_poll_marks_read_and_dedupes():
    fake = FakeOutlook([_graph_msg("m2"), _graph_msg("m1")])  # newest-first
    c = OutlookConnector(tenant_id="t", client=fake)
    sigs = asyncio.run(c.poll())
    assert len(sigs) == 2
    # processed oldest-first so emission order matches arrival order
    assert [s.key["message_id"] for s in sigs] == ["<m1>", "<m2>"]
    assert fake.marked == ["m1", "m2"]          # both marked read
    assert asyncio.run(c.poll()) == []          # nothing unread left


def test_poll_without_mark_read_still_dedupes_in_process():
    fake = FakeOutlook([_graph_msg("m1")])
    c = OutlookConnector(tenant_id="t", client=fake, mark_read=False)
    assert len(asyncio.run(c.poll())) == 1
    assert fake.marked == []                    # left unread on the server
    assert asyncio.run(c.poll()) == []          # but the seen-set suppresses re-emit
