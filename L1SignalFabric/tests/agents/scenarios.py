"""Shared scenario catalog — the single source of truth for both agents.

A *scenario* is one concrete, runnable check against the real code. Each is
tagged with:

  * ``kind``  — UNIT (one component in isolation: a mapper, a verifier, the
                signal model, the bus, the L2 projection) or INTEGRATION
                (several components wired together through the real pipe:
                connector ingest/poll -> bus -> L2 sink, or the FastAPI app).
  * ``covers`` — the source module(s) it exercises, so the agents can render a
                **coverage map** (which components have unit and/or integration
                tests, and which have none).
  * ``asserts`` — a plain-English statement of what the scenario proves, so the
                output reads as documentation, not just PASS/FAIL.

The :class:`TestAgent` runs them and reports per-component; the
:class:`CriticAgent` re-uses the results to validate inputs/outputs against the
canonical contract. Everything here is deterministic and offline (fakes stand in
for every network client; nothing touches a real Slack/Graph/DB endpoint).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# --- make the package importable whether run via `-m` or as a file ----------
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import asyncio  # noqa: E402

from core.bus import InMemoryBus  # noqa: E402
from core.connector import InboundRequest, VerifyOutcome  # noqa: E402
from core.signal import Operation, SignalEvent, SourceSystem  # noqa: E402
from core.watermark import InMemoryWatermarkStore  # noqa: E402
from l2.store import L2JsonlStore  # noqa: E402

# --------------------------------------------------------------------------- #
# Status, kinds, result types
# --------------------------------------------------------------------------- #
PASS = "PASS"
FAIL = "FAIL"

UNIT = "UNIT"
INTEGRATION = "INTEGRATION"


@dataclass
class Probe:
    """What a scenario function returns: the input it fed, the output it got,
    whether it passed, and (optionally) an extra detail line."""

    passed: bool
    input: Any
    output: Any
    detail: str = ""


@dataclass
class Scenario:
    id: str
    title: str
    kind: str                       # UNIT | INTEGRATION
    source: str                     # component group label (e.g. "gmail", "core.bus")
    covers: tuple[str, ...]         # source modules this scenario exercises
    asserts: str                    # plain-English statement of what it proves
    fn: Callable[[], Probe]
    target: bool = False            # part of the email/SharePoint live-integration goal


@dataclass
class ScenarioResult:
    scenario: Scenario
    status: str
    input: Any
    output: Any
    detail: str

    @property
    def id(self) -> str:
        return self.scenario.id

    @property
    def ok(self) -> bool:
        return self.status == PASS


# --------------------------------------------------------------------------- #
# The components we want covered (drives the coverage map)
# --------------------------------------------------------------------------- #
COMPONENTS: list[tuple[str, str]] = [
    ("core.signal", "Canonical SignalEvent model + dedup_id + tz guard"),
    ("core.dedup", "Raw-payload dedup key helper"),
    ("core.bus", "InMemoryBus — dedup / fan-out / replay / isolation"),
    ("core.watermark", "Checkpoint stores — lossless resume"),
    ("core.connector", "Connector contract — verify outcomes / InboundRequest"),
    ("l2.store", "L2 projection — edge / node / SignOffEvent"),
    ("l2.orgmap", "OrgMap graph — upsert nodes/edges, deduped"),
    ("connectors.slack", "Slack connector — verify + ingest + mappers"),
    ("connectors.erp", "ERP outbox connector"),
    ("connectors.gmail", "Gmail connector — Pub/Sub push + history"),
    ("connectors.outlook", "Outlook connector — Graph mail webhook + delta"),
    ("connectors.sharepoint", "SharePoint connector — Graph drives/lists delta"),
    ("connectors.notion", "Notion connector — pages/blocks/properties"),
    ("connectors.database", "Generic SQL CDC/outbox connector"),
    ("connectors.common", "Shared infra — HTTP retry / secrets / writer / webhook"),
    ("demo.email_normalize", "Demo email normalizer (provider-agnostic)"),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_TENANT = "maritime-acme"
_ISO = "2026-06-08T09:00:00+00:00"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _brief(ev: SignalEvent) -> dict[str, Any]:
    return {"source_system": ev.source_system.value, "entity": ev.entity, "key": ev.key}


def _temp_l2() -> L2JsonlStore:
    d = tempfile.mkdtemp(prefix="l2agent_")
    return L2JsonlStore(str(Path(d) / "l2.jsonl"))


async def _pipe(events: list[SignalEvent]) -> tuple[InMemoryBus, L2JsonlStore]:
    """Run events through a real InMemoryBus with the L2 sink subscribed —
    the integration path: publish -> dedup -> fan-out -> L2 projection."""
    bus = InMemoryBus()
    store = _temp_l2()
    bus.subscribe(store.append)
    for e in events:
        await bus.publish(e)
    return bus, store


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


# ---- Slack request helpers ----
def _signed_slack_request(secret: str, body: dict[str, Any], *, ts: Optional[int] = None,
                          tamper: bool = False) -> InboundRequest:
    raw = json.dumps(body).encode("utf-8")
    tss = str(int(ts if ts is not None else time.time()))
    base = b"v0:" + tss.encode() + b":" + raw
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    sig = "v0=" + ("0" * len(digest) if tamper else digest)
    return InboundRequest(headers={"X-Slack-Request-Timestamp": tss, "X-Slack-Signature": sig},
                          body=raw, json=body)


def _slack_message_envelope(event_id: str = "Ev0001") -> dict[str, Any]:
    return {"type": "event_callback", "event_id": event_id, "team_id": "T-FLEET",
            "event": {"type": "message", "channel": "C-CREW", "user": "U-0001",
                      "text": "Sign-off confirmed", "ts": "1719980964.000100"}}


# ---- Gmail/Graph helpers ----
def _gmail_envelope(notif: dict, message_id: str = "p1") -> dict:
    data = base64.b64encode(json.dumps(notif).encode()).decode()
    return {"message": {"data": data, "messageId": message_id}}


def _graph_msg(mid: str = "m1", subject: str = "Hello", categories=None) -> dict:
    return {"id": mid, "internetMessageId": f"<{mid}>", "conversationId": "c1",
            "from": {"emailAddress": {"address": "a@x"}},
            "toRecipients": [{"emailAddress": {"address": "b@y"}}],
            "ccRecipients": [{"emailAddress": {"address": "c@z"}}],
            "subject": subject, "categories": categories or [],
            "receivedDateTime": "2024-06-01T10:00:00Z"}


def _sp_drive_item(iid: str = "i1", removed: bool = False) -> dict:
    item = {"id": iid, "name": "Crew.xlsx", "webUrl": "http://sp/i1", "size": 10,
            "file": {"mimeType": "app/xlsx"},
            "parentReference": {"driveId": "d1", "path": "/drive/root:/Crew"},
            "lastModifiedDateTime": "2024-06-01T10:00:00Z",
            "createdBy": {"user": {"id": "u1", "displayName": "Al"}}}
    if removed:
        item["@removed"] = {"state": "deleted"}
    return item


# ============================================================================ #
# UNIT SCENARIOS
# ============================================================================ #
# ---- core.signal ----
def _u_signal_tz_and_dedup() -> Probe:
    ev = SignalEvent(entity="crew", key={"crew_id": "C1"}, source_system=SourceSystem.DATABASE,
                     tenant_id=_TENANT, timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))
    deterministic = ev.dedup_id == ev.dedup_id and len(ev.dedup_id) == 64
    naive_rejected = False
    try:
        SignalEvent(entity="x", key={"k": 1}, source_system=SourceSystem.SLACK,
                    tenant_id="t", timestamp=datetime(2026, 6, 8))  # naive
    except Exception:
        naive_rejected = True
    ok = deterministic and naive_rejected and ev.operation == Operation.DELTA
    return Probe(ok, "valid event + a naive-timestamp event",
                 {"dedup_len": len(ev.dedup_id), "naive_rejected": naive_rejected,
                  "op": ev.operation.value}, "")


def _u_signal_dedup_changes_with_key() -> Probe:
    a = SignalEvent(entity="m", key={"id": "1"}, source_system=SourceSystem.SLACK,
                    tenant_id="t", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))
    b = SignalEvent(entity="m", key={"id": "2"}, source_system=SourceSystem.SLACK,
                    tenant_id="t", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))
    ok = a.dedup_id != b.dedup_id
    return Probe(ok, "two events differing only by key", {"distinct": ok}, "")


# ---- core.dedup ----
def _u_dedup_key() -> Probe:
    from core.dedup import dedup_key
    base = dict(source="SLACK", entity="message", occurred_at=_ISO, natural_keys=["id"])
    k1 = dedup_key(payload={"id": "A", "x": 1}, **base)
    k2 = dedup_key(payload={"id": "A", "x": 2}, **base)   # same natural key → same id
    k3 = dedup_key(payload={"id": "B"}, **base)
    ok = k1 == k2 and k1 != k3 and len(k1) == 64
    return Probe(ok, "same natural key twice + a different key",
                 {"stable": k1 == k2, "distinct": k1 != k3}, "")


# ---- core.bus ----
def _sample_event() -> SignalEvent:
    return SignalEvent(entity="message", key={"channel_id": "C-CREW", "ts": "1.1"},
                       source_system=SourceSystem.SLACK, tenant_id=_TENANT,
                       timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))


def _u_bus_dedup() -> Probe:
    bus = InMemoryBus()
    got: list = []
    bus.subscribe(got.append)
    ev = _sample_event()
    _run(bus.publish(ev))
    _run(bus.publish(ev))
    ok = len(got) == 1 and bus.duplicate_count == 1
    return Probe(ok, "publish the same event twice",
                 {"delivered": len(got), "dropped": bus.duplicate_count}, "")


def _u_bus_isolation_and_replay() -> Probe:
    bus = InMemoryBus()
    good: list = []

    def bad(_e):
        raise RuntimeError("poison")

    bus.subscribe(bad)
    bus.subscribe(good.append)
    raised = False
    try:
        _run(bus.publish(_sample_event()))
    except Exception:
        raised = True
    ok = (not raised) and len(good) == 1 and len(bus.replay()) == 1
    return Probe(ok, "one throwing + one good subscriber, then replay()",
                 {"propagated": raised, "good_got": len(good), "replay": len(bus.replay())}, "")


# ---- core.watermark ----
def _u_watermark_roundtrip() -> Probe:
    wm = InMemoryWatermarkStore()
    default = wm.get("erp", 0)
    wm.set("erp", 7)
    ok = default == 0 and wm.get("erp", 0) == 7
    return Probe(ok, "get(default)=0, set(7), get()", {"default": default, "after": wm.get("erp", 0)}, "")


# ---- core.connector ----
def _u_connector_contract() -> Probe:
    from core.connector import VerifyResult
    ok = (VerifyResult.ok().outcome == VerifyOutcome.OK
          and VerifyResult.challenge_with("x").challenge == "x"
          and VerifyResult.reject("r").outcome == VerifyOutcome.REJECT)
    req = InboundRequest(headers={"X-A": "1"}, query={"t": "2"})
    ok = ok and req.header("x-a") == "1"   # case-insensitive header lookup
    return Probe(ok, "VerifyResult constructors + case-insensitive header()",
                 {"header_ci": req.header("x-a")}, "")


# ---- l2.store ----
def _u_l2_projection_kinds() -> Probe:
    slack = SignalEvent(entity="message", key={"k": 1}, source_system=SourceSystem.SLACK,
                        tenant_id="t", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
                        data={"user": "U1", "channel": "C1"})
    signoff = SignalEvent(entity="email", key={"k": 2}, source_system=SourceSystem.GMAIL,
                          tenant_id="t", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
                          metadata={"l2Intent": "CREATE_SIGNOFF_EVENT"}, data={"subject": "s"})
    node = SignalEvent(entity="crew", key={"k": 3}, source_system=SourceSystem.DATABASE,
                       tenant_id="t", timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc))
    kinds = (L2JsonlStore.project(slack)["kind"], L2JsonlStore.project(signoff)["kind"],
             L2JsonlStore.project(node)["kind"])
    ok = kinds == ("edge", "signoff_event", "node")
    return Probe(ok, "Slack / sign-off / DB events", {"kinds": kinds}, "")


# ---- connectors.slack ----
def _u_slack_handshake() -> Probe:
    from connectors.slack import SlackConnector
    body = {"type": "url_verification", "challenge": "abc123"}
    res = SlackConnector(tenant_id=_TENANT).verify(
        InboundRequest(json=body, body=json.dumps(body).encode()))
    ok = res.outcome == VerifyOutcome.CHALLENGE and res.challenge == "abc123"
    return Probe(ok, "url_verification body", {"outcome": res.outcome.value, "challenge": res.challenge}, "")


def _u_slack_signature() -> Probe:
    from connectors.slack import SlackConnector
    secret = "s3cr3t"
    c = SlackConnector(tenant_id=_TENANT, signing_secret=secret)
    good = c.verify(_signed_slack_request(secret, _slack_message_envelope()))
    bad = c.verify(_signed_slack_request(secret, _slack_message_envelope(), tamper=True))
    stale = c.verify(_signed_slack_request(secret, _slack_message_envelope(), ts=int(time.time()) - 100_000))
    ok = (good.outcome == VerifyOutcome.OK and bad.outcome == VerifyOutcome.REJECT
          and stale.outcome == VerifyOutcome.REJECT)
    return Probe(ok, "valid / tampered / stale signatures",
                 {"valid": good.outcome.value, "tampered": bad.outcome.value, "stale": stale.outcome.value}, "")


def _u_slack_mappers_and_dedup() -> Probe:
    from connectors.slack import SlackConnector
    c = SlackConnector(tenant_id=_TENANT)
    env = _slack_message_envelope(event_id="Ev-DUP")
    first = _run(c.ingest(env))
    second = _run(c.ingest(env))
    unknown = _run(c.ingest({"type": "event_callback", "event_id": "Ev9",
                             "event": {"type": "channel_archive"}}))
    ok = (len(first) == 1 and first[0].entity == "message" and second == [] and unknown == [])
    return Probe(ok, "message ingest, duplicate event_id, unknown type",
                 {"first": len(first), "dup": len(second), "unknown": len(unknown)}, "")


# ---- connectors.erp ----
def _erp_adapter_three():
    from connectors.erp import InMemoryOutboxAdapter
    a = InMemoryOutboxAdapter()
    a.append(table="crew", op="insert", occurred_at=_ISO, data={"crew_id": "CR-1001", "name": "Arjun"})
    a.append(table="contract", op="insert", occurred_at=_ISO, data={"contract_id": "K-1", "crew_id": "CR-1001"})
    a.append(table="vessel_port", op="update", occurred_at=_ISO, data={"vessel_id": "VSL-001"})
    return a


def _u_erp_poll_three() -> Probe:
    from connectors.erp import ErpConnector
    c = ErpConnector(tenant_id=_TENANT, adapter=_erp_adapter_three())
    evs = _run(c.poll())
    systems = {e.source_system for e in evs}
    ok = systems == {SourceSystem.CREW_DB, SourceSystem.CONTRACT_CLM, SourceSystem.VESSEL_PORT_DB}
    return Probe(ok, "outbox: crew + contract + vessel_port rows", [_brief(e) for e in evs], "")


# ---- connectors.gmail ----
def _u_gmail_verify() -> Probe:
    from connectors.gmail import GmailConnector
    from connectors.gmail.verify import verify_pubsub_token
    tok_ok = verify_pubsub_token(configured_token="s3cret", received_token="s3cret").ok
    tok_bad = verify_pubsub_token(configured_token="s3cret", received_token="x").ok
    c = GmailConnector(tenant_id="t", pubsub_token="s3cret")
    q_ok = c.verify(InboundRequest(query={"token": "s3cret"})).outcome.value
    q_bad = c.verify(InboundRequest(query={})).outcome.value
    ok = tok_ok and not tok_bad and q_ok == "ok" and q_bad == "reject"
    return Probe(ok, "Pub/Sub token match/mismatch (header+query)",
                 {"match": tok_ok, "mismatch": tok_bad, "query_ok": q_ok, "query_bad": q_bad}, "")


def _u_gmail_metadata_no_body() -> Probe:
    from connectors.gmail import message_metadata_to_record, record_to_signal
    msg = {"id": "m1", "threadId": "th1", "internalDate": "1717236000000", "labelIds": ["INBOX"],
           "payload": {"headers": [{"name": "From", "value": "a@x"},
                                   {"name": "To", "value": "b@y, c@z"},
                                   {"name": "Subject", "value": "Sign-off notification"}]}}
    rec = message_metadata_to_record(msg)
    sig = record_to_signal(rec, "t")
    ok = (rec["from"] == "a@x" and rec["to"] == ["b@y", "c@z"] and "body" not in rec
          and rec["snippet_present"] is False and sig.source_system == SourceSystem.GMAIL
          and sig.metadata.get("l2Intent") == "CREATE_SIGNOFF_EVENT")
    return Probe(ok, "Gmail metadata record (no body) + sign-off intent",
                 {"from": rec["from"], "to": rec["to"], "body_absent": "body" not in rec,
                  "intent": sig.metadata.get("l2Intent")}, "")


def _u_gmail_ingest_dedup() -> Probe:
    from connectors.gmail import GmailConnector
    c = GmailConnector(tenant_id="t")
    notif = {"historyId": "9", "_messages": [
        {"message_id": "mz", "from": "a@x", "to": ["b@y"], "subject": "hi", "labels": [],
         "sent_at": "2024-06-01T10:00:00Z"}]}
    env = _gmail_envelope(notif)
    first = _run(c.ingest(env))
    second = _run(c.ingest(env))   # same Pub/Sub messageId redelivered
    ok = len(first) == 1 and first[0].key == {"message_id": "mz"} and second == []
    return Probe(ok, "inline-message envelope ingest + messageId dedup",
                 {"first": len(first), "redelivery": len(second)}, "")


def _u_gmail_history_watermark() -> Probe:
    from connectors.gmail import GmailConnector

    class FakeGmail:
        api_calls = 0
        rate_limit_hits = 0

        def history_list(self, start):
            yield {"id": "100", "messagesAdded": [{"message": {"id": "m1"}}]}

        def get_message_metadata(self, mid):
            return {"id": mid, "internalDate": "1717236000000", "labelIds": [],
                    "payload": {"headers": [{"name": "From", "value": "a@x"},
                                            {"name": "Subject", "value": "s"}]}}

    c = GmailConnector(tenant_id="t", client=FakeGmail())
    sigs = _run(c.ingest(_gmail_envelope({"historyId": "200"}, "px")))
    ok = len(sigs) == 1 and sigs[0].data["from"] == "a@x" and c.position() == "200"
    return Probe(ok, "history.list expansion advances the historyId watermark",
                 {"emitted": len(sigs), "position": c.position()}, "")


# ---- connectors.outlook ----
def _u_outlook_mapper() -> Probe:
    from connectors.outlook.mappers import graph_message_to_record, message_to_signal
    rec = graph_message_to_record(_graph_msg())
    sig = message_to_signal(_graph_msg(categories=["crew/sign-off"]), "t")
    ok = (rec["from"] == "a@x" and rec["to"] == ["b@y"] and rec["thread_id"] == "c1"
          and sig.source_system == SourceSystem.OUTLOOK
          and sig.metadata.get("l2Intent") == "CREATE_SIGNOFF_EVENT")
    return Probe(ok, "Graph message -> record + OUTLOOK sign-off signal",
                 {"from": rec["from"], "thread": rec["thread_id"], "intent": sig.metadata.get("l2Intent")}, "")


def _u_outlook_verify() -> Probe:
    from connectors.outlook import OutlookConnector
    c = OutlookConnector(tenant_id="t", client_state="kept")
    hs = c.verify(InboundRequest(query={"validationToken": "tok"}))
    good = c.verify(InboundRequest(json={"value": [{"clientState": "kept"}]}))
    bad = c.verify(InboundRequest(json={"value": [{"clientState": "nope"}]}))
    ok = (hs.outcome.value == "challenge" and hs.challenge == "tok"
          and good.outcome.value == "ok" and bad.outcome.value == "reject")
    return Probe(ok, "validationToken handshake + clientState verify",
                 {"handshake": hs.challenge, "good": good.outcome.value, "bad": bad.outcome.value}, "")


def _u_outlook_delta_watermark() -> Probe:
    from connectors.outlook import OutlookConnector

    class FakeOutlook:
        api_calls = 0
        rate_limit_hits = 0

        def delta(self, folder="inbox", start=None, select=""):
            if start:
                return [], "link2"
            return [_graph_msg("m1"), {"id": "m2", "@removed": {"reason": "deleted"}}], "link2"

    c = OutlookConnector(tenant_id="t", client=FakeOutlook())
    sigs = _run(c.poll())
    again = _run(c.poll())
    ok = len(sigs) == 1 and c.position() == "link2" and again == []
    return Probe(ok, "delta poll skips @removed, advances deltaLink, re-poll empty",
                 {"emitted": len(sigs), "position": c.position(), "repoll": len(again)}, "")


# ---- connectors.sharepoint ----
def _u_sharepoint_mappers() -> Probe:
    from connectors.sharepoint import drive_item_to_signal, list_item_to_signal
    d = drive_item_to_signal(_sp_drive_item(), "t", "d1")
    li = list_item_to_signal({"id": "L1", "fields": {"Title": "V7", "Status": "Open"},
                              "lastModifiedDateTime": "2024-06-02T00:00:00Z"}, "t", "site1", "list1")
    ok = (d.entity == "drive_item" and d.key == {"drive_id": "d1", "item_id": "i1"}
          and d.source_system == SourceSystem.SHAREPOINT and li.entity == "list_item"
          and li.data["fields"]["Title"] == "V7")
    return Probe(ok, "driveItem + listItem -> SignalEvent",
                 {"drive_key": d.key, "list_title": li.data["fields"]["Title"]}, "")


def _u_sharepoint_verify() -> Probe:
    from connectors.sharepoint import SharePointConnector
    vr = SharePointConnector(tenant_id="t").verify(InboundRequest(query={"validationToken": "v"}))
    ok = vr.outcome.value == "challenge" and vr.challenge == "v"
    return Probe(ok, "subscription validationToken handshake", {"challenge": vr.challenge}, "")


def _u_sharepoint_delta_watermark() -> Probe:
    from connectors.sharepoint import DriveTarget, ListTarget, SharePointConnector

    class FakeSP:
        api_calls = 0
        rate_limit_hits = 0

        def drive_delta(self, drive_id, start=None):
            if start:
                return [], "dlink2"
            return [_sp_drive_item("i1"), {"root": {}, "id": "root"}], "dlink2"

        def list_delta(self, site_id, list_id, start=None):
            if start:
                return [], "llink2"
            return [{"id": "L1", "fields": {"Title": "V7"},
                     "lastModifiedDateTime": "2024-06-02T00:00:00Z"}], "llink2"

    c = SharePointConnector(tenant_id="t", client=FakeSP(),
                            targets=[DriveTarget("d1"), ListTarget("site1", "list1")])
    sigs = _run(c.poll())
    entities = sorted(s.entity for s in sigs)
    snap = c.position()
    ok = (entities == ["drive_item", "list_item"] and snap["sharepoint:drive:d1"] == "dlink2"
          and snap["sharepoint:list:site1:list1"] == "llink2" and _run(c.poll()) == [])
    return Probe(ok, "per-target delta tokens; root anchor skipped; re-poll empty",
                 {"entities": entities, "watermarks": snap}, "")


# ---- connectors.notion ----
class _FakeNotion:
    api_calls = 5
    rate_limit_hits = 0

    def __init__(self, pages):
        self._pages = pages
        self._blocks = {
            "p1": [
                {"id": "b1", "type": "heading_1",
                 "heading_1": {"rich_text": [{"plain_text": "Title"}]}, "has_children": False},
                {"id": "b2", "type": "bulleted_list_item",
                 "bulleted_list_item": {"rich_text": [{"plain_text": "item"}]}, "has_children": True},
                {"id": "b3", "type": "code",
                 "code": {"rich_text": [{"plain_text": "x=1"}], "language": "python"}, "has_children": False},
            ],
            "b2": [{"id": "b2a", "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "nested"}]}, "has_children": False}],
        }

    def get_all_blocks(self, block_id):
        return self._blocks.get(block_id, [])

    def search_all(self, query="", filter_type=None):
        yield from self._pages

    def query_database_all(self, db_id):
        return iter([])


def _notion_page(pid, edited):
    return {"object": "page", "id": pid, "url": f"http://n/{pid}",
            "created_time": "2024-01-01T00:00:00.000Z", "last_edited_time": edited,
            "parent": {"type": "workspace"},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "My Page"}]}},
            "created_by": {"id": "u1", "type": "person", "person": {"email": "a@b.io"}, "name": "Al"},
            "last_edited_by": {"id": "u1"}}


def _u_notion_block_parser() -> Probe:
    from connectors.common import StructuredLogger
    from connectors.notion.block_parser import BlockParser
    bp = BlockParser(_FakeNotion([]), StructuredLogger(console_output=False))
    content = bp.extract_page_content("p1")
    ok = ("Title" in content and "- item" in content and "nested" in content
          and "```python" in content and bp.blocks_fetched == 4)
    return Probe(ok, "recursive block parse (headings, nested list, code fence)",
                 {"blocks_fetched": bp.blocks_fetched, "has_code": "```python" in content}, "")


def _u_notion_properties() -> Probe:
    from connectors.notion.block_parser import extract_property_value, extract_simplified_properties
    cb = extract_property_value({"type": "checkbox", "checkbox": True})
    ms = extract_property_value({"type": "multi_select", "multi_select": [{"name": "a"}, {"name": "b"}]})
    simp = extract_simplified_properties({"S": {"type": "select", "select": {"name": "x"}}})
    ok = cb == "Yes" and ms == "a, b" and simp == {"S": "x"}
    return Probe(ok, "property extraction (checkbox / multi_select / select)",
                 {"checkbox": cb, "multi_select": ms, "select": simp}, "")


def _u_notion_poll() -> Probe:
    from connectors.notion import NotionConnector
    c = NotionConnector(tenant_id="t", client=_FakeNotion([_notion_page("p1", "2024-06-01T10:00:00.000Z")]))
    sigs = _run(c.poll())
    again = _run(c.poll())
    ok = (len(sigs) == 1 and sigs[0].entity == "page" and sigs[0].key == {"page_id": "p1"}
          and sigs[0].data["title"] == "My Page" and again == [])
    return Probe(ok, "incremental poll by last_edited_time; re-poll empty",
                 {"emitted": len(sigs), "title": sigs[0].data["title"] if sigs else None,
                  "repoll": len(again)}, "")


# ---- connectors.database ----
def _u_database_poll_resume() -> Probe:
    from connectors.database import DatabaseConnector, InMemoryOutboxAdapter
    a = InMemoryOutboxAdapter(key_field="crew_id")
    a.append(table="crew", op="INSERT", occurred_at="2024-06-01T10:00:00Z", row={"crew_id": "C1"})
    a.append(table="crew", op="UPDATE", occurred_at="2024-06-01T10:05:00Z", row={"crew_id": "C2"})
    wm = InMemoryWatermarkStore()
    c = DatabaseConnector(tenant_id="t", adapter=a, watermarks=wm)
    sigs = _run(c.poll())
    c2 = DatabaseConnector(tenant_id="t", adapter=a, watermarks=wm)   # restart on shared watermark
    resumed = _run(c2.poll())
    ok = ([s.key["crew_id"] for s in sigs] == ["C1", "C2"]
          and all(s.source_system == SourceSystem.DATABASE for s in sigs)
          and c.position() == 2 and resumed == [])
    return Probe(ok, "outbox poll -> DATABASE DELTAs; fresh connector resumes (no replay)",
                 {"keys": [s.key["crew_id"] for s in sigs], "position": c.position(),
                  "resumed": len(resumed)}, "")


def _u_database_dedup_stable() -> Probe:
    from connectors.database import DatabaseConnector, InMemoryOutboxAdapter
    a = InMemoryOutboxAdapter(key_field="id")
    a.append(table="crew", op="INSERT", occurred_at="2024-06-01T10:00:00Z", row={"id": "C1"})
    s1 = _run(DatabaseConnector(tenant_id="t", adapter=a).poll())[0]
    s2 = _run(DatabaseConnector(tenant_id="t", adapter=a).poll())[0]
    ok = s1.dedup_id == s2.dedup_id
    return Probe(ok, "dedup_id stable across re-poll (at-least-once safe)",
                 {"stable": ok}, "")


# ---- connectors.common ----
class _Resp:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text
        self.reason = "err"

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1
        return self._responses.pop(0)


def _u_common_http_retry() -> Probe:
    from connectors.common import RateLimitedClient
    c = RateLimitedClient(base_url="https://x", rate_limit_delay_ms=0,
                          max_rate_limit_errors=2, sleep=lambda *_: None)
    c._session = _FakeSession([_Resp(429, headers={"Retry-After": "0"}), _Resp(200, {"ok": True})])
    body = c.get("m")
    ok = body["ok"] is True and c.rate_limit_hits == 1 and c._consecutive_rate_limits == 0
    return Probe(ok, "429 -> retry -> 200, hit counted and reset on success",
                 {"ok": body.get("ok"), "rate_limit_hits": c.rate_limit_hits}, "")


def _u_common_secrets_and_webhook() -> Probe:
    from connectors.common import parse_timestamp, verify_graph_webhook
    ts = parse_timestamp("2024-01-01T12:30:00Z").isoformat()
    none = parse_timestamp(None)
    hs = verify_graph_webhook(InboundRequest(query={"validationToken": "x"}))
    bad = verify_graph_webhook(InboundRequest(json={"value": [{"clientState": "no"}]}), client_state="yes")
    ok = (ts == "2024-01-01T12:30:00+00:00" and none is None
          and hs.outcome.value == "challenge" and bad.outcome.value == "reject")
    return Probe(ok, "parse_timestamp + Graph webhook handshake/clientState",
                 {"ts": ts, "handshake": hs.outcome.value, "bad_state": bad.outcome.value}, "")


def _u_common_writer() -> Probe:
    from connectors.common import OutputWriter
    d = tempfile.mkdtemp(prefix="writeragent_")
    w = OutputWriter(str(d), source="slack", entity="messages")
    ev = SignalEvent(entity="message", key={"id": 1}, source_system=SourceSystem.SLACK,
                     tenant_id="t", timestamp=datetime.now(timezone.utc))
    with w:
        w.write_event(ev)
    ok = w.count == 1 and (Path(d) / "slack.jsonl").exists()
    return Probe(ok, "OutputWriter emits one JSONL record", {"count": w.count}, "")


# ---- demo.email_normalize ----
def _u_demo_email() -> Probe:
    from demo.email_normalize import email_to_signal
    routine = email_to_signal({"message_id": "<m1@x>", "subject": "Berth confirmation",
                               "sent_at": _ISO, "labels": ["crew/ops"], "from": {}, "to": []}, _TENANT)[0]
    signoff = email_to_signal({"message_id": "<m2@x>", "subject": "Sign-Off Notification",
                               "sent_at": _ISO, "labels": ["crew/sign-off"], "from": {}, "to": []}, _TENANT)[0]
    ok = (routine.source_system == SourceSystem.EMAIL and "body" not in routine.data
          and routine.timestamp.tzinfo is not None
          and signoff.metadata.get("l2Intent") == "CREATE_SIGNOFF_EVENT")
    return Probe(ok, "routine + sign-off email normalize (metadata-only, tz-aware)",
                 {"body_absent": "body" not in routine.data, "intent": signoff.metadata.get("l2Intent")}, "")


# ---- l2.store crew sign-on/off detail extraction (Day-2 live enrichment) ----
def _u_crew_parse() -> Probe:
    from l2.store import extract_crew_change
    labelled = extract_crew_change(
        "Crew Sign-Off Notification\nName: Diego Silva\nRole: Oiler\nEmail: diego@x.io\n"
        "Crew ID: CR-1001\nVessel: MV Pacific Dawn\nPort: Rotterdam")
    inline = extract_crew_change("Sign-off confirmed: Diego Silva (Oiler) ex MV Pacific Dawn at Rotterdam.")
    chatter = extract_crew_change("can someone sign off on the PR when you get a sec")
    # a sign-ON that references the relieved person's sign-off must stay sign_on
    reliever = extract_crew_change("Sign-on completed: Arjun Nair (Oiler) relieving Diego "
                                   "who signed off MV Pacific Dawn at Rotterdam.")
    ok = (labelled == {"action": "sign_off", "crew_member": "Diego Silva", "role": "Oiler",
                       "email": "diego@x.io", "crew_id": "CR-1001",
                       "vessel": "MV Pacific Dawn", "port": "Rotterdam"}
          and inline.get("crew_member") == "Diego Silva" and inline.get("role") == "Oiler"
          and inline.get("vessel") == "MV Pacific Dawn" and inline.get("port") == "Rotterdam"
          and chatter is None
          and reliever.get("action") == "sign_on" and reliever.get("crew_member") == "Arjun Nair")
    return Probe(ok, "labelled + inline + reliever sign-on + casual chatter",
                 {"labelled": labelled, "inline": inline, "reliever": reliever, "chatter": chatter}, "")


def _u_crew_mrkdwn() -> Probe:
    from l2.store import extract_crew_change
    real = extract_crew_change(  # exact Slack mrkdwn italics on labels
        "_Crew Sign-Off Notification_\n_Crew Member:_ Diego Silva (Oiler)\n"
        " _Vessel:_ MV Pacific Dawn\n _Port:_ Rotterdam")
    underscores = extract_crew_change(  # underscores inside email/id must survive
        "Sign-off\n_Name:_ Ada Lee\n_Email:_ ada_lee@x.io\n_Crew ID:_ CR_2002")
    partial = extract_crew_change("_Sign-Off_\n_Vessel:_ MV Orion Star\n_Port:_ Busan")
    ok = (real.get("role") == "Oiler" and real.get("crew_member") == "Diego Silva"
          and real.get("vessel") == "MV Pacific Dawn" and real.get("port") == "Rotterdam"
          and underscores.get("email") == "ada_lee@x.io" and underscores.get("crew_id") == "CR_2002"
          and set(partial) == {"action", "vessel", "port"})
    return Probe(ok, "Slack italic labels parsed; email/id underscores kept; only-present fields",
                 {"real": real, "underscores": underscores, "partial": partial}, "")


def _u_l2_crew_props() -> Probe:
    ev = SignalEvent(entity="message", key={"channel_id": "C1", "ts": "1.1"},
                     source_system=SourceSystem.SLACK, tenant_id="t",
                     timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
                     data={"user": "U1", "channel": "C1", "channel_name": "#crew-changes",
                           "text": "Sign-Off Notification\nName: Diego Silva\nRole: Oiler\n"
                                   "Vessel: MV Pacific Dawn\nPort: Rotterdam"})
    p = L2JsonlStore.project(ev)["props"]
    ok = (p.get("channel") == "#crew-changes" and p.get("channel_id") == "C1"
          and p.get("crew_member") == "Diego Silva" and p.get("role") == "Oiler"
          and p.get("vessel") == "MV Pacific Dawn" and p.get("port") == "Rotterdam"
          and p.get("action") == "sign_off")
    return Probe(ok, "Slack sign-off message", p, "")


# ---- connectors.slack Web-API enrichment (channel/user id -> name) ----
class _FakeSlackClient:
    api_calls = 0
    rate_limit_hits = 0

    def get_channel_info(self, cid):
        from connectors.slack.models import ChannelInfo
        return ChannelInfo(id=cid, name="crew-changes", is_member=True)

    def get_user_info(self, uid):
        return {"profile": {"display_name": "Diego Silva", "email": "diego@x.io"}}


def _u_slack_enrich() -> Probe:
    from connectors.slack import SlackConnector
    c = SlackConnector(tenant_id="t", client=_FakeSlackClient())
    raw = {"type": "event_callback", "event_id": "Ev-E", "team_id": "T",
           "event": {"type": "message", "channel": "C9", "user": "U9", "text": "hi", "ts": "1.1"}}
    ev = _run(c.ingest(raw))[0]
    ok = ev.data.get("channel_name") == "#crew-changes" and ev.data.get("user_name") == "Diego Silva"
    return Probe(ok, "ingest with an injected Web-API client",
                 {"channel_name": ev.data.get("channel_name"), "user_name": ev.data.get("user_name")}, "")


# ---- l2.orgmap upsert graph ----
def _u_orgmap_upsert() -> Probe:
    from l2.orgmap import OrgMap
    om = OrgMap()
    slack = SignalEvent(entity="message", key={"channel_id": "C1", "ts": "1.1"},
                        source_system=SourceSystem.SLACK, tenant_id="t",
                        timestamp=datetime(2026, 6, 8, tzinfo=timezone.utc),
                        data={"user": "U1", "channel": "C1", "channel_name": "#crew-changes",
                              "text": "Sign-Off Notification\nName: Diego Silva\nRole: Oiler\n"
                                      "Vessel: MV Pacific Dawn\nPort: Rotterdam"})
    rec = L2JsonlStore.project(slack)
    om.upsert(rec)
    om.upsert(rec)                       # identical event again → upsert, not duplicate
    s = om.stats()
    person = om.nodes.get("person:U1")
    labels = set(s["by_node_label"])
    ok = (person is not None and person["count"] == 2
          and {"Person", "Channel", "Crew", "Vessel", "Port"} <= labels
          and "ON_VESSEL" in s["by_edge_label"])
    return Probe(ok, "two identical Slack sign-offs",
                 {"nodes": s["nodes"], "edges": s["edges"],
                  "person_count": person["count"] if person else None, "labels": sorted(labels)}, "")


# ---- core.bus dead-letter queue ----
def _u_bus_dlq() -> Probe:
    bus = InMemoryBus()
    good: list = []

    def bad(_e):
        raise RuntimeError("poison")

    bus.subscribe(bad)
    bus.subscribe(good.append)
    _run(bus.publish(_sample_event()))
    dl = bus.dlq.recent()
    ok = (len(good) == 1 and bus.dlq.count == 1 and bus.stats()["dead_letters"] == 1
          and bool(dl) and dl[0]["error"].startswith("RuntimeError"))
    return Probe(ok, "publish with a poison subscriber + a good one",
                 {"good_received": len(good), "dlq_count": bus.dlq.count,
                  "dlq_error": dl[0]["error"] if dl else None}, "")


# ============================================================================ #
# INTEGRATION SCENARIOS — through the real bus + L2 sink (or the FastAPI app)
# ============================================================================ #
def _i_slack_to_l2() -> Probe:
    from connectors.slack import SlackConnector
    evs = _run(SlackConnector(tenant_id=_TENANT).ingest(_slack_message_envelope()))
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["total"] == 1 and c["by_kind"].get("edge") == 1
    return Probe(ok, "Slack message: ingest -> bus -> L2 OrgMap edge", c, "")


def _i_erp_to_l2() -> Probe:
    from connectors.erp import ErpConnector
    evs = _run(ErpConnector(tenant_id=_TENANT, adapter=_erp_adapter_three()).poll())
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["total"] == 3 and c["by_kind"].get("node") == 3
    return Probe(ok, "ERP poll -> bus -> three L2 entity nodes", c, "")


def _i_gmail_signoff_to_l2() -> Probe:
    from connectors.gmail import GmailConnector
    notif = {"historyId": "9", "_messages": [
        {"message_id": "mz", "from": "a@x", "to": ["b@y"], "subject": "Sign-off notification",
         "labels": [], "sent_at": "2024-06-01T10:00:00Z"}]}
    evs = _run(GmailConnector(tenant_id=_TENANT).ingest(_gmail_envelope(notif)))
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["signoff"] == 1 and c["by_kind"].get("signoff_event") == 1
    return Probe(ok, "Gmail sign-off: ingest -> bus -> L2 SignOffEvent node", c, "")


def _i_outlook_to_l2() -> Probe:
    from connectors.outlook import OutlookConnector
    body = {"value": [{"subscriptionId": "s1", "resourceData": {"id": "m1"},
                       "_message": _graph_msg(categories=["crew/sign-off"])}]}
    evs = _run(OutlookConnector(tenant_id=_TENANT).ingest(body))
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["signoff"] == 1
    return Probe(ok, "Outlook sign-off notification -> bus -> L2 SignOffEvent", c, "")


def _i_sharepoint_to_l2() -> Probe:
    from connectors.sharepoint import DriveTarget, ListTarget, SharePointConnector

    class FakeSP:
        api_calls = 0
        rate_limit_hits = 0

        def drive_delta(self, drive_id, start=None):
            return ([_sp_drive_item("i1")], "dlink") if not start else ([], "dlink")

        def list_delta(self, site_id, list_id, start=None):
            return ([{"id": "L1", "fields": {"Title": "V7"},
                      "lastModifiedDateTime": "2024-06-02T00:00:00Z"}], "llink") if not start else ([], "llink")

    evs = _run(SharePointConnector(tenant_id=_TENANT, client=FakeSP(),
                                   targets=[DriveTarget("d1"), ListTarget("site1", "list1")]).poll())
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["total"] == 2 and c["by_kind"].get("node") == 2
    return Probe(ok, "SharePoint delta poll -> bus -> two L2 nodes (drive + list item)", c, "")


def _i_notion_to_l2() -> Probe:
    from connectors.notion import NotionConnector
    evs = _run(NotionConnector(tenant_id=_TENANT,
                               client=_FakeNotion([_notion_page("p1", "2024-06-01T10:00:00.000Z")])).poll())
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["total"] == 1 and c["by_kind"].get("node") == 1
    return Probe(ok, "Notion poll -> bus -> L2 node", c, "")


def _i_database_to_l2() -> Probe:
    from connectors.database import DatabaseConnector, InMemoryOutboxAdapter
    a = InMemoryOutboxAdapter(key_field="crew_id")
    a.append(table="crew", op="INSERT", occurred_at=_ISO, row={"crew_id": "C1"})
    evs = _run(DatabaseConnector(tenant_id=_TENANT, adapter=a).poll())
    _, store = _run(_pipe(evs))
    c = store.counts()
    ok = c["total"] == 1 and c["by_kind"].get("node") == 1
    return Probe(ok, "Database outbox poll -> bus -> L2 node", c, "")


def _i_bus_dedup_across_repoll() -> Probe:
    # at-least-once delivery: the same row polled twice must reach L2 exactly once
    from connectors.database import DatabaseConnector, InMemoryOutboxAdapter
    a = InMemoryOutboxAdapter(key_field="id")
    a.append(table="crew", op="INSERT", occurred_at=_ISO, row={"id": "C1"})
    e1 = _run(DatabaseConnector(tenant_id=_TENANT, adapter=a).poll())
    e2 = _run(DatabaseConnector(tenant_id=_TENANT, adapter=a).poll())  # re-poll same row (fresh cursor)
    bus, store = _run(_pipe(e1 + e2))
    ok = store.counts()["total"] == 1 and bus.duplicate_count == 1
    return Probe(ok, "duplicate poll -> bus dedup -> exactly-once at L2",
                 {"l2_total": store.counts()["total"], "dropped": bus.duplicate_count}, "")


def _i_app_healthz_and_slack() -> Probe:
    # full FastAPI app: route -> connector -> bus -> L2 (temp store, never the real data dir)
    from fastapi.testclient import TestClient

    from api.app import create_app
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(Path(tempfile.mkdtemp(prefix="apiagent_")) / "l2.jsonl")
    cfg.slack_signing_secret = ""   # dev-bypass: this scenario posts unsigned events
    cfg.slack_token = ""            # no live Slack enrichment in the offline scenario
    client = TestClient(create_app(settings=cfg))
    hz = client.get("/healthz")
    hs = client.post("/slack/events", json={"type": "url_verification", "challenge": "xyz"})
    msg = client.post("/slack/events", json=_slack_message_envelope(event_id="Ev-API"))
    ok = (hz.status_code == 200 and hz.json()["status"] == "ok"
          and hs.status_code == 200 and hs.text.strip('"') == "xyz"
          and msg.status_code == 200 and msg.json().get("ingested") == 1)
    return Probe(ok, "FastAPI app: /healthz + Slack handshake + message ingest",
                 {"healthz": hz.json().get("status"), "handshake": hs.text, "ingest": msg.json()}, "")


def _i_slack_route_raw() -> Probe:
    # the live /slack/events route must attach the raw payload so the dashboard
    # drawer can render raw → normalized → L2 for real (non-demo) events.
    from fastapi.testclient import TestClient

    from api.app import create_app
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(Path(tempfile.mkdtemp(prefix="rawagent_")) / "l2.jsonl")
    cfg.slack_signing_secret = ""   # dev-bypass for the unsigned test post
    cfg.slack_token = ""
    client = TestClient(create_app(settings=cfg))
    r = client.post("/slack/events", json=_slack_message_envelope(event_id="Ev-RAW"))
    bus = client.app.state.bus
    raw = bus.recent[-1].get("raw") if getattr(bus, "recent", None) else None
    ok = (r.status_code == 200 and r.json().get("ingested") == 1
          and raw is not None and raw.get("event_id") == "Ev-RAW")
    return Probe(ok, "POST /slack/events then inspect the dashboard payload",
                 {"ingested": r.json().get("ingested"), "raw_event_id": (raw or {}).get("event_id")}, "")


def _i_orgmap_graph() -> Probe:
    # a Slack sign-off flows through the app and upserts the OrgMap graph
    from fastapi.testclient import TestClient

    from api.app import create_app
    from config import Settings
    cfg = Settings()
    cfg.l2_store_path = str(Path(tempfile.mkdtemp(prefix="omagent_")) / "l2.jsonl")
    cfg.slack_signing_secret = ""
    cfg.slack_token = ""
    client = TestClient(create_app(settings=cfg))
    env = _slack_message_envelope(event_id="Ev-OM")
    env["event"]["text"] = ("Sign-Off Notification\nName: Diego Silva\nRole: Oiler\n"
                            "Vessel: MV Pacific Dawn\nPort: Rotterdam")
    client.post("/slack/events", json=env)
    om = client.get("/orgmap").json()
    dlq = client.get("/bus/dlq").json()
    labels = {n["label"] for n in om["nodes"]}
    ok = (om["stats"]["nodes"] >= 4 and {"Person", "Crew", "Vessel"} <= labels
          and dlq.get("count") == 0)
    return Probe(ok, "POST /slack/events → GET /orgmap + /bus/dlq",
                 {"nodes": om["stats"]["nodes"], "labels": sorted(labels), "dlq": dlq.get("count")}, "")


# ============================================================================ #
# Registry
# ============================================================================ #
def _s(id, title, kind, source, covers, asserts, fn, target=False) -> Scenario:
    return Scenario(id, title, kind, source, tuple(covers), asserts, fn, target)


SCENARIOS: list[Scenario] = [
    # ---------------- UNIT ----------------
    _s("u-signal-contract", "SignalEvent contract", UNIT, "core.signal", ["core.signal"],
       "naive timestamps are rejected; dedup_id is a deterministic sha256; operation defaults to DELTA",
       _u_signal_tz_and_dedup),
    _s("u-signal-dedup-key", "dedup_id varies with key", UNIT, "core.signal", ["core.signal"],
       "two events differing only by natural key get different dedup_ids", _u_signal_dedup_changes_with_key),
    _s("u-dedup-helper", "raw-payload dedup_key", UNIT, "core.dedup", ["core.dedup"],
       "dedup_key() is stable for equal payloads and distinct for different ones", _u_dedup_key),
    _s("u-bus-dedup", "bus drops duplicates", UNIT, "core.bus", ["core.bus"],
       "publishing the same dedup_id twice delivers once and counts one drop", _u_bus_dedup),
    _s("u-bus-isolation", "subscriber isolation + replay", UNIT, "core.bus", ["core.bus"],
       "a throwing subscriber is isolated; the good one still receives; replay() holds the event",
       _u_bus_isolation_and_replay),
    _s("u-watermark", "watermark get/set", UNIT, "core.watermark", ["core.watermark"],
       "a watermark store returns the default until set, then the stored cursor", _u_watermark_roundtrip),
    _s("u-connector-contract", "connector contract", UNIT, "core.connector", ["core.connector"],
       "VerifyResult ok/challenge/reject constructors and case-insensitive InboundRequest.header()",
       _u_connector_contract),
    _s("u-l2-projection", "L2 projection kinds", UNIT, "l2.store", ["l2.store"],
       "Slack->edge, sign-off->signoff_event, other->node", _u_l2_projection_kinds),
    _s("u-slack-handshake", "Slack url_verification", UNIT, "slack", ["connectors.slack"],
       "the handshake echoes the challenge value", _u_slack_handshake),
    _s("u-slack-signature", "Slack HMAC signature", UNIT, "slack", ["connectors.slack"],
       "valid signatures pass; tampered and stale (replay-window) ones are rejected", _u_slack_signature),
    _s("u-slack-ingest", "Slack ingest + dedup", UNIT, "slack", ["connectors.slack"],
       "a message normalizes to one event; duplicate event_id and unknown types yield nothing",
       _u_slack_mappers_and_dedup),
    _s("u-erp-poll", "ERP outbox fan-out", UNIT, "erp", ["connectors.erp"],
       "one outbox feed maps rows to the three ERP source systems", _u_erp_poll_three),
    _s("u-gmail-verify", "Gmail Pub/Sub token", UNIT, "gmail", ["connectors.gmail"],
       "the Pub/Sub token is verified from header or ?token= query (mismatch rejected)",
       _u_gmail_verify, target=True),
    _s("u-gmail-metadata", "Gmail metadata + sign-off", UNIT, "gmail", ["connectors.gmail"],
       "headers map to from/to with no body; a sign-off subject sets l2Intent; source is GMAIL",
       _u_gmail_metadata_no_body, target=True),
    _s("u-gmail-ingest", "Gmail ingest + dedup", UNIT, "gmail", ["connectors.gmail"],
       "an inline-message Pub/Sub envelope ingests once; redelivery of the same messageId is dropped",
       _u_gmail_ingest_dedup, target=True),
    _s("u-gmail-history", "Gmail history watermark", UNIT, "gmail", ["connectors.gmail"],
       "history.list expansion emits the message and advances the historyId watermark",
       _u_gmail_history_watermark, target=True),
    _s("u-outlook-mapper", "Outlook Graph mapper", UNIT, "outlook", ["connectors.outlook"],
       "a Graph message maps to a record and an OUTLOOK sign-off signal", _u_outlook_mapper, target=True),
    _s("u-outlook-verify", "Outlook webhook verify", UNIT, "outlook", ["connectors.outlook"],
       "validationToken handshake + clientState secret verification", _u_outlook_verify, target=True),
    _s("u-outlook-delta", "Outlook delta watermark", UNIT, "outlook", ["connectors.outlook"],
       "delta poll skips @removed items, advances the deltaLink, and re-poll is empty",
       _u_outlook_delta_watermark, target=True),
    _s("u-sharepoint-mappers", "SharePoint mappers", UNIT, "sharepoint", ["connectors.sharepoint"],
       "driveItem and listItem map to canonical SHAREPOINT SignalEvents", _u_sharepoint_mappers, target=True),
    _s("u-sharepoint-verify", "SharePoint webhook verify", UNIT, "sharepoint", ["connectors.sharepoint"],
       "the subscription validationToken handshake is echoed", _u_sharepoint_verify, target=True),
    _s("u-sharepoint-delta", "SharePoint per-target delta", UNIT, "sharepoint", ["connectors.sharepoint"],
       "drive + list deltas carry per-target change tokens; the root anchor is skipped; re-poll empty",
       _u_sharepoint_delta_watermark, target=True),
    _s("u-notion-blocks", "Notion block parser", UNIT, "notion", ["connectors.notion"],
       "the block parser flattens nested lists and renders code fences recursively", _u_notion_block_parser),
    _s("u-notion-props", "Notion properties", UNIT, "notion", ["connectors.notion"],
       "checkbox/multi_select/select properties extract to readable values", _u_notion_properties),
    _s("u-notion-poll", "Notion incremental poll", UNIT, "notion", ["connectors.notion"],
       "pages poll once by last_edited_time and re-poll emits nothing", _u_notion_poll),
    _s("u-database-poll", "Database poll + resume", UNIT, "database", ["connectors.database"],
       "outbox rows become DATABASE DELTAs; a fresh connector resumes without replay", _u_database_poll_resume),
    _s("u-database-dedup", "Database dedup stable", UNIT, "database", ["connectors.database"],
       "dedup_id is identical across re-polls, so at-least-once delivery is safe", _u_database_dedup_stable),
    _s("u-common-http", "HTTP retry/backoff", UNIT, "common", ["connectors.common"],
       "the shared HTTP client retries a 429 then succeeds, counting and resetting the hit",
       _u_common_http_retry),
    _s("u-common-webhook", "secrets + Graph webhook", UNIT, "common", ["connectors.common"],
       "parse_timestamp normalizes to UTC; the Graph webhook verifier handshakes and checks clientState",
       _u_common_secrets_and_webhook),
    _s("u-common-writer", "Conduit output writer", UNIT, "common", ["connectors.common"],
       "the OutputWriter emits one JSONL record per event", _u_common_writer),
    _s("u-demo-email", "demo email normalizer", UNIT, "demo.email_normalize", ["demo.email_normalize"],
       "routine and sign-off emails normalize metadata-only with tz-aware timestamps", _u_demo_email),
    _s("u-crew-parse", "crew sign-off parser", UNIT, "l2.store", ["l2.store"],
       "labelled + inline sign-off notices parse to crew_member/role/email/crew_id/vessel/port; chatter→none",
       _u_crew_parse),
    _s("u-crew-mrkdwn", "crew parser handles Slack mrkdwn", UNIT, "l2.store", ["l2.store"],
       "Slack italic labels (_Role:_) parse; underscores in emails/ids preserved; only present fields kept",
       _u_crew_mrkdwn),
    _s("u-l2-crew-props", "L2 props enriched with crew + channel", UNIT, "l2.store", ["l2.store"],
       "a Slack sign-off message projects with crew details + resolved channel name/id in props",
       _u_l2_crew_props),
    _s("u-slack-enrich", "Slack channel/user name resolution", UNIT, "slack", ["connectors.slack"],
       "the connector resolves channel & user ids to human names via the Web API (injected client)",
       _u_slack_enrich),
    _s("u-orgmap-upsert", "OrgMap upsert graph", UNIT, "l2.orgmap", ["l2.orgmap"],
       "identical events upsert (not duplicate) into deduped Person/Channel/Crew/Vessel/Port nodes + edges",
       _u_orgmap_upsert),
    _s("u-bus-dlq", "bus dead-letter queue", UNIT, "core.bus", ["core.bus"],
       "a poison subscriber is isolated and recorded in the DLQ; the good subscriber still receives",
       _u_bus_dlq),

    # ---------------- INTEGRATION ----------------
    _s("i-slack-pipe", "Slack -> bus -> L2", INTEGRATION, "slack", ["connectors.slack", "core.bus", "l2.store"],
       "a Slack message flows ingest -> bus -> L2 and lands as one OrgMap edge", _i_slack_to_l2),
    _s("i-erp-pipe", "ERP -> bus -> L2", INTEGRATION, "erp", ["connectors.erp", "core.bus", "l2.store"],
       "an ERP poll flows through the bus and lands as three L2 entity nodes", _i_erp_to_l2),
    _s("i-gmail-pipe", "Gmail sign-off -> SignOffEvent", INTEGRATION, "gmail",
       ["connectors.gmail", "core.bus", "l2.store"],
       "a Gmail sign-off email flows ingest -> bus -> L2 and materializes a SignOffEvent node",
       _i_gmail_signoff_to_l2, target=True),
    _s("i-outlook-pipe", "Outlook sign-off -> SignOffEvent", INTEGRATION, "outlook",
       ["connectors.outlook", "core.bus", "l2.store"],
       "an Outlook sign-off notification flows through the bus and materializes a SignOffEvent",
       _i_outlook_to_l2, target=True),
    _s("i-sharepoint-pipe", "SharePoint -> bus -> L2", INTEGRATION, "sharepoint",
       ["connectors.sharepoint", "core.bus", "l2.store"],
       "a SharePoint delta poll flows through the bus and lands as two L2 nodes", _i_sharepoint_to_l2, target=True),
    _s("i-notion-pipe", "Notion -> bus -> L2", INTEGRATION, "notion",
       ["connectors.notion", "core.bus", "l2.store"],
       "a Notion page poll flows through the bus and lands as one L2 node", _i_notion_to_l2),
    _s("i-database-pipe", "Database -> bus -> L2", INTEGRATION, "database",
       ["connectors.database", "core.bus", "l2.store"],
       "a Database outbox poll flows through the bus and lands as one L2 node", _i_database_to_l2),
    _s("i-bus-exactly-once", "exactly-once across re-poll", INTEGRATION, "core.bus",
       ["connectors.database", "core.bus", "l2.store"],
       "the same row polled twice is deduplicated by the bus and reaches L2 exactly once",
       _i_bus_dedup_across_repoll),
    _s("i-app-pipe", "FastAPI app end-to-end", INTEGRATION, "api",
       ["connectors.slack", "core.bus", "l2.store"],
       "the running app answers /healthz and the Slack route ingests a handshake + a message",
       _i_app_healthz_and_slack),
    _s("i-slack-route-raw", "live route captures raw ingress", INTEGRATION, "api",
       ["connectors.slack", "core.bus", "l2.store"],
       "POST /slack/events attaches the raw payload so the dashboard drawer shows raw → normalized → L2",
       _i_slack_route_raw),
    _s("i-orgmap-graph", "live OrgMap graph upsert", INTEGRATION, "api",
       ["l2.orgmap", "core.bus", "l2.store"],
       "a Slack sign-off through the app upserts the OrgMap (Person/Crew/Vessel) and /bus/dlq stays empty",
       _i_orgmap_graph),
]


def run_scenario(s: Scenario) -> ScenarioResult:
    try:
        p = s.fn()
        return ScenarioResult(s, PASS if p.passed else FAIL, p.input, p.output, p.detail or s.asserts)
    except Exception as exc:  # a scenario that blows up is a reported failure, never a crash
        import traceback
        tb = traceback.format_exc().strip().splitlines()[-1]
        return ScenarioResult(s, FAIL, "<scenario raised>", repr(exc),
                              f"unexpected error: {tb}")


def run_all(kind: Optional[str] = None, source: Optional[str] = None) -> list[ScenarioResult]:
    scs = [s for s in SCENARIOS
           if (kind is None or s.kind == kind) and (source is None or s.source == source)]
    return [run_scenario(s) for s in scs]


# --------------------------------------------------------------------------- #
# Coverage map (component -> which scenarios exercise it, split by kind)
# --------------------------------------------------------------------------- #
def coverage(results: list[ScenarioResult]) -> dict[str, dict[str, Any]]:
    """For each declared component, the scenarios covering it, split unit/integration."""
    cov: dict[str, dict[str, Any]] = {
        mod: {"desc": desc, "unit": [], "integration": [], "unit_pass": 0, "int_pass": 0}
        for mod, desc in COMPONENTS
    }
    for r in results:
        for mod in r.scenario.covers:
            if mod not in cov:
                continue
            bucket = "unit" if r.scenario.kind == UNIT else "integration"
            cov[mod][bucket].append(r)
            if r.ok:
                cov[mod]["unit_pass" if bucket == "unit" else "int_pass"] += 1
    return cov


# --------------------------------------------------------------------------- #
# Project pytest suite — run the repo's pre-existing integrated tests
# --------------------------------------------------------------------------- #
_PYTEST_LINE = re.compile(
    r"^(?P<file>tests/[^:]+\.py)::(?P<name>.+?)\s+"
    r"(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")


def discover_pytest_files() -> list[str]:
    """The repo's integrated pytest files (excludes this agent package)."""
    root = _ROOT / "tests"
    return sorted(
        str(p.relative_to(_ROOT)).replace("\\", "/")
        for p in root.glob("test_*.py")
    )


def run_pytest_suite(timeout: int = 180) -> dict[str, Any]:
    """Run the repo's pytest suite as a subprocess and parse per-file results.

    Runs with a **temp ``L2_STORE_PATH``** so the suite's ``create_app()`` calls
    never truncate the dashboard's live L2 store. Returns a structured summary;
    ``available`` is False (with a note) when pytest can't run.
    """
    files = discover_pytest_files()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["L2_STORE_PATH"] = str(Path(tempfile.mkdtemp(prefix="pytest_l2_")) / "l2.jsonl")
    env["ERP_WATERMARK_PATH"] = ""          # force in-memory watermarks
    env["DATABASE_WATERMARK_PATH"] = ""
    # neutralize inherited Slack secrets so the suite runs on dev-safe defaults
    # (unsigned test requests rely on dev-bypass; no live API enrichment)
    env["SLACK_SIGNING_SECRET"] = ""
    env["SLACK_TOKEN"] = ""
    env["SLACK_DEV_ALLOW_UNVERIFIED"] = "1"
    cmd = [sys.executable, "-m", "pytest", "tests", "--ignore=tests/agents",
           "-v", "-p", "no:cacheprovider", "--no-header", "-rN"]
    try:
        proc = subprocess.run(cmd, cwd=str(_ROOT), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        return {"available": False, "files": files, "note": f"could not launch pytest: {exc}"}
    except subprocess.TimeoutExpired:
        return {"available": False, "files": files, "note": f"pytest timed out after {timeout}s"}

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "No module named pytest" in out or proc.returncode == 4:
        return {"available": False, "files": files, "returncode": proc.returncode,
                "note": "pytest is not installed for this interpreter — `python -m pip install pytest`"}

    by_file: dict[str, dict[str, Any]] = {}
    tests: list[tuple[str, str, str]] = []   # (file, test_name, status) — every test case
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    _norm = {"passed": "passed", "xpass": "passed", "failed": "failed", "error": "error",
             "skipped": "skipped", "xfail": "skipped"}
    for ln in out.splitlines():
        m = _PYTEST_LINE.match(ln.strip())
        if not m:
            continue
        f, name, st = m.group("file"), m.group("name"), _norm[m.group("status").lower()]
        d = by_file.setdefault(f, {"passed": 0, "failed": 0, "skipped": 0, "error": 0,
                                   "tests": [], "failures": []})
        d[st] += 1
        counts[st] += 1
        d["tests"].append((name, st))
        tests.append((f, name, st))
        if st in ("failed", "error"):
            d["failures"].append(name)

    total = sum(counts.values())
    summary = next((l.strip() for l in reversed(out.splitlines())
                    if (" passed" in l or " failed" in l or " error" in l) and " in " in l), "")
    return {"available": True, "returncode": proc.returncode, "files": files,
            "by_file": by_file, "tests": tests, "counts": counts, "total": total, "summary": summary}


# --------------------------------------------------------------------------- #
# Capability probes (used by the critic for the current-state snapshot)
# --------------------------------------------------------------------------- #
def probe_capabilities() -> dict[str, bool]:
    import core.bus as busmod
    return {
        "slack_connector": _importable("connectors.slack"),
        "erp_connector": _importable("connectors.erp"),
        "gmail_connector": _importable("connectors.gmail"),
        "outlook_connector": _importable("connectors.outlook"),
        "sharepoint_connector": _importable("connectors.sharepoint"),
        "notion_connector": _importable("connectors.notion"),
        "database_connector": _importable("connectors.database"),
        "common_infra": _importable("connectors.common"),
        "inmemory_bus": True,
        "l2_sink": _importable("l2"),
        "orgmap_graph": _importable("l2") and hasattr(importlib.import_module("l2"), "OrgMap"),
        "email_normalizer_demo": _importable("demo.email_normalize"),
        "sharepoint_enum": "SHAREPOINT" in SourceSystem.__members__,
        "redis_streams_bus": hasattr(busmod, "RedisStreamsBus"),
        "durable_dlq": hasattr(busmod, "DeadLetterQueue"),
    }
