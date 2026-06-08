"""Critic Agent — validates the pipeline's inputs and outputs, then critiques it.

Where the Test Agent answers *"does each scenario pass?"*, the Critic answers
*"are the inputs and outputs correct, and what should we do next?"* — framed on
our actual task: **the email + SharePoint live integration**.

It does three things:

  1. INPUT/OUTPUT VALIDATION — builds golden canonical events from each live
     source and checks them against the ``SignalEvent`` contract (tz-aware
     timestamps, required fields, deterministic ``dedup_id``, DELTA invariant,
     schemaVersion), plus the L2 projection shape. Violations are reported.
  2. CURRENT-STATE SNAPSHOT — what the pipeline has today (capability probes +
     the Test Agent's PASS/FAIL results).
  3. CRITIQUE — what we need to correct/build for the live integration, and how
     to make it more advanced (a ranked roadmap).

Run:
    python -m tests.agents.critic_agent
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.signal import Operation, SignalEvent, SourceSystem  # noqa: E402
from l2.store import L2JsonlStore  # noqa: E402
from tests.agents.scenarios import (  # noqa: E402
    ScenarioResult, probe_capabilities, run_all,
)


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _quiet_logs() -> None:
    import logging
    logging.disable(logging.CRITICAL)


# --------------------------------------------------------------------------- #
# 1. Contract validation of inputs/outputs
# --------------------------------------------------------------------------- #
def validate_event(ev: SignalEvent) -> list[tuple[str, str]]:
    """Return (severity, message) findings for one canonical event.

    severity is ERROR (hard contract break) or WARN (recommended-but-missing).
    An empty list means the event is fully contract-clean.
    """
    out: list[tuple[str, str]] = []

    if not ev.entity:
        out.append(("ERROR", "entity is empty"))
    if not isinstance(ev.key, dict) or not ev.key:
        out.append(("ERROR", "key must be a non-empty natural key"))
    if not isinstance(ev.source_system, SourceSystem):
        out.append(("ERROR", "source_system must be a SourceSystem enum member"))
    if not ev.tenant_id:
        out.append(("ERROR", "tenant_id is empty (multi-tenant isolation needs it)"))
    if ev.timestamp.tzinfo is None:
        out.append(("ERROR", "timestamp is not timezone-aware"))
    if ev.extracted_at.tzinfo is None:
        out.append(("ERROR", "extracted_at is not timezone-aware"))
    if ev.operation != Operation.DELTA:
        out.append(("ERROR", f"L1 streams must be DELTA, got {ev.operation.value}"))

    # dedup_id must exist and be deterministic (same event -> same id)
    d1, d2 = ev.dedup_id, ev.dedup_id
    if not d1 or len(d1) != 64:
        out.append(("ERROR", "dedup_id is missing or not a sha256 hex digest"))
    if d1 != d2:
        out.append(("ERROR", "dedup_id is not deterministic"))

    if not (ev.metadata or {}).get("schemaVersion"):
        out.append(("WARN", "metadata.schemaVersion is absent (recommended for evolution)"))
    if ev.lineage is None:
        out.append(("WARN", "lineage is absent (provenance/audit recommended)"))

    return out


def validate_l2_record(rec: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for field in ("id", "kind", "label", "source_system", "key", "ts"):
        if field not in rec:
            out.append(("ERROR", f"L2 record missing '{field}'"))
    if rec.get("kind") not in {"node", "edge", "signoff_event"}:
        out.append(("ERROR", f"L2 record has unexpected kind: {rec.get('kind')!r}"))
    return out


def _golden_events() -> dict[str, SignalEvent]:
    """Build one canonical event per live source (the inputs/outputs to validate)."""
    import asyncio

    from connectors.erp import ErpConnector, InMemoryOutboxAdapter
    from connectors.slack import SlackConnector
    from demo.email_normalize import email_to_signal

    slack = SlackConnector(tenant_id="maritime-acme")
    slack_ev = asyncio.run(slack.ingest({
        "type": "event_callback", "event_id": "Ev1", "team_id": "T",
        "event": {"type": "message", "channel": "C-CREW", "user": "U-1",
                  "text": "hi", "ts": "1719980964.000100"}}))[0]

    adapter = InMemoryOutboxAdapter()
    adapter.append(table="crew", op="insert", occurred_at="2026-06-08T09:00:00+00:00",
                   data={"crew_id": "CR-1001", "name": "Arjun Sharma", "rank": "Master"})
    erp = ErpConnector(tenant_id="maritime-acme", adapter=adapter)
    erp_ev = asyncio.run(erp.poll())[0]

    email_ev = email_to_signal({
        "message_id": "<m1@mail>", "thread_id": "thr-1",
        "from": {"name": "Priya", "address": "p@x"}, "to": [],
        "subject": "Sign-Off Notification: Arjun Sharma", "sent_at": "2026-06-08T09:00:00+00:00",
        "labels": ["crew/sign-off"]}, "maritime-acme")[0]

    return {"slack/message": slack_ev, "erp/crew": erp_ev, "email/sign-off": email_ev}


# --------------------------------------------------------------------------- #
# 2 + 3. Critique content (the live-integration knowledge)
# --------------------------------------------------------------------------- #
# scenario id -> severity for the "what to correct" ranking
_SEVERITY = {
    "sp-enum": "BLOCKER", "sp-connector": "BLOCKER",
    "sp-webhook-validation": "HIGH", "sp-clientstate": "HIGH", "sp-normalize": "HIGH",
    "sp-delta-checkpoint": "HIGH", "sp-subscription-renewal": "MEDIUM",
    "sp-signoff-unification": "MEDIUM", "email-live-connector": "HIGH",
}
_SEV_ORDER = {"BLOCKER": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

ADVANCED_ROADMAP = [
    ("Unify the Graph machinery", "email + sharepoint",
     "Gmail-via-Graph and SharePoint both ride Microsoft Graph change notifications + /delta. "
     "Build ONE Graph subscription/verification/delta layer and share it across the mail and "
     "SharePoint connectors instead of two bespoke push paths."),
    ("Cross-source sign-off dedup", "email + sharepoint",
     "The same sign-off can arrive as an email AND a SharePoint document. Give SignOffEvent a "
     "stable natural key (crew_id + sign_off_date) so the two collapse to one node via the bus "
     "dedup_id rather than creating duplicates downstream."),
    ("Rich payloads with encryption", "sharepoint",
     "Subscribe with encryptionCertificate to receive resource data inline (encryptedContent), "
     "decrypt with the app cert, and skip the extra Graph GET — lower latency, fewer throttling hits."),
    ("Durable checkpoint + DLQ", "platform",
     "Move watermarks/delta tokens and the dead-letter path off in-memory onto the Day-4 "
     "RedisStreamsBus (XADD/XCLAIM/pending-entries-list) so resume and poison-event handling "
     "survive restarts."),
    ("Backpressure + throttling", "platform",
     "Graph enforces request limits and returns 429/Retry-After. Add token-bucket rate limiting, "
     "exponential backoff, and a bounded queue so a notification storm can't overrun the connector."),
    ("Schema registry + versioning", "platform",
     "Promote metadata.schemaVersion to an enforced contract (a registry) so SharePoint/email "
     "payload shape changes are validated at ingest and downstream consumers can migrate safely."),
    ("PII & attachment policy", "email + sharepoint",
     "Both sources can carry PII and documents. Define an explicit redaction/retention policy, "
     "optional body/attachment capture with field-level masking, and audit via lineage.checksum."),
    ("Observability", "platform",
     "Per-connector metrics (events/sec, lag vs deltaLink, dup-rate, subscriber-fail count, "
     "renewal failures) on the dashboard + alerts, so the live integration is operable, not just runnable."),
    ("Structured crew-notice parsing", "slack + email",
     "The crew sign-on/off props are lifted by regex/mrkdwn heuristics today. Parse Slack Block Kit "
     "rich_text blocks (already in the event) and add an NER/LLM fallback for free-form notices, so "
     "crew_member/role/vessel/port survive arbitrary phrasing — and reconcile the parsed crew_id "
     "against the ERP crew row."),
    ("Persistent enrichment cache", "slack",
     "Channel/user name lookups are cached in-process; persist them (Redis) and pre-warm via "
     "conversations.list/users.list so a restart doesn't re-hit the Slack API under rate limits."),
]


def _requirement_lines(results: list[ScenarioResult]) -> list[tuple[str, str, str]]:
    """From failed target scenarios, build (severity, id, fix) correction items."""
    items: list[tuple[str, str, str]] = []
    for r in results:
        if r.scenario.target and not r.ok:
            sev = _SEVERITY.get(r.id, "MEDIUM")
            items.append((sev, r.id, r.detail))
    items.sort(key=lambda x: (_SEV_ORDER.get(x[0], 9), x[1]))
    return items


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class CriticAgent:
    name = "critic-agent"

    def run(self) -> int:
        results = run_all()
        caps = probe_capabilities()
        golden = _golden_events()

        print("=" * 96)
        print(" L1 SignalFabric — CRITIC AGENT")
        print(" Validates inputs/outputs against the canonical contract, then critiques the")
        print(" pipeline for our task: the EMAIL + SHAREPOINT live integration.")
        print("=" * 96)

        contract_clean = self._section_validation(golden)
        self._section_scoreboard(results, caps)
        self._section_current(results, caps)
        self._section_correct(results)
        self._section_advanced()

        # exit code: red only if a live, implemented input/output breaks the contract
        return 0 if contract_clean else 1

    # ---- section 1: input/output validation -------------------------------
    def _section_validation(self, golden: dict[str, SignalEvent]) -> bool:
        print("\n[1] INPUT / OUTPUT VALIDATION (canonical SignalEvent contract)")
        print("-" * 96)
        clean = True
        for label, ev in golden.items():
            findings = validate_event(ev)
            l2 = L2JsonlStore.project(ev)
            findings += validate_l2_record(l2)
            errors = [m for s, m in findings if s == "ERROR"]
            warns = [m for s, m in findings if s == "WARN"]
            verdict = "VALID" if not errors else "INVALID"
            if errors:
                clean = False
            print(f"  {label:<16} -> {verdict}   (output L2 kind={l2.get('kind')}, "
                  f"dedup_id={ev.dedup_id[:8]}…)")
            for m in errors:
                print(f"        ERROR: {m}")
            for m in warns:
                print(f"        warn : {m}")
        print(f"\n  Verdict: every live source emits a {'CONTRACT-CLEAN' if clean else 'NON-CONFORMING'} "
              "SignalEvent that the L2 sink can project.")
        return clean

    # ---- section 2: scoreboard --------------------------------------------
    def _section_scoreboard(self, results: list[ScenarioResult], caps: dict[str, bool]) -> None:
        impl = [r for r in results if not r.scenario.target]
        tgt = [r for r in results if r.scenario.target]
        impl_pass = sum(1 for r in impl if r.ok)
        tgt_pass = sum(1 for r in tgt if r.ok)
        print("\n[2] READINESS SCOREBOARD")
        print("-" * 96)
        print(f"  implemented pipeline : {impl_pass}/{len(impl)} scenarios PASS")
        print(f"  live integration     : {tgt_pass}/{len(tgt)} target scenarios PASS "
              f"(email + SharePoint)")
        bar_done = "#" * tgt_pass
        bar_todo = "." * (len(tgt) - tgt_pass)
        print(f"  live progress        : [{bar_done}{bar_todo}] {tgt_pass}/{len(tgt)}")

    # ---- section 3: what we have ------------------------------------------
    def _section_current(self, results: list[ScenarioResult], caps: dict[str, bool]) -> None:
        print("\n[3] WHAT WE HAVE CURRENTLY")
        print("-" * 96)
        have = [
            ("Slack ingress (LIVE)", caps["slack_connector"],
             "HMAC verify + ingest + event_id dedup; resolves channel/user ids → names; "
             "live route captures raw payload for the dashboard drawer"),
            ("ERP ingress", caps["erp_connector"],
             "outbox poll across 3 systems + watermark + lossless resume"),
            ("Gmail (LIVE)", caps["gmail_connector"],
             "Pub/Sub push + OIDC/token verify + history backfill, metadata-only + sign-off intent"),
            ("Outlook (LIVE)", caps["outlook_connector"],
             "Graph mail webhook (validationToken + clientState) + delta poll watermark"),
            ("SharePoint (LIVE)", caps["sharepoint_connector"],
             "Graph drives/lists delta poll + per-target change tokens + webhook handshake"),
            ("Notion", caps["notion_connector"],
             "pages/databases/blocks pull + incremental last_edited_time watermark"),
            ("Database (generic CDC)", caps["database_connector"],
             "outbox + updated-at adapters (in-memory + real sqlite/SQLAlchemy)"),
            ("Shared infra", caps["common_infra"],
             "rate-limited/retrying HTTP, secrets, Graph webhook verify, JSONL writer, metrics"),
            ("Event bus", caps["inmemory_bus"],
             "InMemoryBus: central dedup_id dedup + subscriber fan-out + replay + isolation"),
            ("L2 sink", caps["l2_sink"],
             "projects to OrgMap edge / entity node / SignOffEvent node; parses crew sign-on/off "
             "notices (crew_member, role, email, crew_id, vessel, port — Slack-mrkdwn aware) into props"),
            ("OrgMap graph", caps.get("orgmap_graph"),
             "in-memory knowledge graph upserted from the L2 stream (Person/Channel/Crew/Vessel/Port/"
             "SignOffEvent nodes + edges, deduped) with a live viewer tab (GET /orgmap)"),
            ("Dead-letter queue", caps["durable_dlq"],
             "a failing L2 sink/subscriber is isolated AND recorded in the bus DLQ (GET /bus/dlq), "
             "surfaced on the dashboard — no longer just a logged seam"),
        ]
        for name, ok, desc in have:
            print(f"  [{'x' if ok else ' '}] {name:<22} {desc}")
        print("\n  Still open (platform hardening, not the email/SharePoint feature):")
        print(f"  [{'x' if caps['redis_streams_bus'] else ' '}] RedisStreamsBus (durable transport, Day-4)")
        print("  [ ] Persist the DLQ + OrgMap off-process (Redis/graph DB) so they survive restarts")

    # ---- section 4: what to correct / build -------------------------------
    def _section_correct(self, results: list[ScenarioResult]) -> None:
        print("\n[4] WHAT WE NEED TO CORRECT / BUILD  (for the email + SharePoint live integration)")
        print("-" * 96)
        items = _requirement_lines(results)
        if not items:
            print("  Nothing outstanding — the live integration targets all pass. ")
            return
        for sev, sid, fix in items:
            print(f"  [{sev:<7}] {sid}")
            print(f"            {fix}")
        print("\n  Build order (suggested): SHAREPOINT enum + connector skeleton (BLOCKER) ->")
        print("  validationToken handshake + clientState verify (HIGH) -> notification->SignalEvent")
        print("  normalizer + delta-token watermark (HIGH) -> subscription renewal + sign-off")
        print("  unification (MEDIUM). Promote email to a live connector in parallel (HIGH).")

    # ---- section 5: how to make it more advanced --------------------------
    def _section_advanced(self) -> None:
        print("\n[5] HOW TO MAKE IT MORE ADVANCED")
        print("-" * 96)
        for title, scope, desc in ADVANCED_ROADMAP:
            print(f"  * {title}  [{scope}]")
            print(f"      {desc}")
        print("=" * 96)


def main() -> int:
    _utf8_stdout()
    _quiet_logs()
    return CriticAgent().run()


if __name__ == "__main__":
    raise SystemExit(main())
