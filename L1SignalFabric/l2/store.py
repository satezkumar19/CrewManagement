"""Minimal L2 store + sink — the downstream end of the demo pipe.

The L1 demo goal is to prove the **whole pipe**:
    ingress → normalizer → bus → L2 store → live tail

This is a lightweight stand-in for the real L2 Operational Knowledge Graph
(OrgMap + SignOffEvent nodes). It subscribes to the bus and *projects* each
canonical `SignalEvent` into an append-only **L2 JSONL store**:

  * SLACK message/reaction/join  → OrgMap edge (person ↔ channel)
  * EMAIL (metadata)             → OrgMap edge (sender ↔ recipients)
  * EMAIL with l2Intent=sign-off → **SignOffEvent** node
  * ERP crew/contract/vessel     → entity node

It is intentionally simple and file-based; Sruthy's real L2 sink (graph upsert)
implements the same "subscribe to the bus, write downstream" contract.
"""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional

from core.signal import SignalEvent

# source/entity → L2 edge label (OrgMap tribal-knowledge edges)
_SLACK_LABEL = {"message": "POSTED_IN", "reaction": "REACTED_IN", "channel_join": "MEMBER_OF"}
_ERP_LABEL = {"crew": "Crew", "contract": "Contract", "vessel_port": "Vessel"}

# --- crew sign-on / sign-off detail extraction --------------------------------
# A Slack message body or e-mail subject announcing a crew change carries
# semi-structured details (crew member, rank, vessel, port). We lift them into
# the L2 ``props`` so the graph record matches the ERP/e-mail shape instead of
# leaving the text opaque. Handles both labelled ("Crew Member: …", "Vessel: …",
# "Port: …") and inline ("… Diego Silva (Oiler) … MV Pacific Dawn at Rotterdam")
# phrasings.
_ACTION_PATTERNS = (
    ("sign_off", re.compile(r"sign[\s-]?off|signing off|signed off", re.I)),
    ("sign_on", re.compile(r"sign[\s-]?on|signing on|signed on", re.I)),
)
# any "Key: Value" line is captured dynamically; these aliases map a label to its
# canonical prop name (so "Name", "Seafarer", "Crew Member" all → crew_member).
_LABEL_ALIASES = {
    "name": "crew_member", "crew member": "crew_member", "crew name": "crew_member",
    "seafarer": "crew_member", "crew": "crew_member",
    "role": "role", "rank": "role", "designation": "role", "position": "role",
    "email": "email", "e-mail": "email", "mail": "email", "email id": "email",
    "crew id": "crew_id", "crewid": "crew_id", "id": "crew_id",
    "seafarer id": "crew_id", "employee id": "crew_id",
    "vessel": "vessel", "ship": "vessel", "vessel name": "vessel",
    "port": "port", "relief port": "port", "sign off port": "port", "sign on port": "port",
}
_LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z ./_-]*?)\s*:\s*(.+?)\s*$")
_NAME_RANK = re.compile(r"([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)+)\s*\(([^)]+)\)")
_VESSEL_INLINE = re.compile(r"\b(MV\s+[A-Z][\w'’]+(?:\s+[A-Z][\w'’]+)*)")
_PORT_INLINE = re.compile(r"\bat\s+([A-Z][\w()/.'’ ]+?)(?:[.,;:—\-]|$)")
_EMAIL_INLINE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CREW_ID_INLINE = re.compile(r"\b(CR[-_]?\d{2,}|CREW[-_]?\d{2,})\b", re.I)

_DETAIL_KEYS = ("crew_member", "role", "email", "crew_id", "vessel", "port")


# Slack renders labels with mrkdwn emphasis (e.g. "_Crew Member:_ …", "*Vessel*").
# Strip the emphasis markers before parsing so labelled fields are recognised.
# Underscores *inside* a word (emails, ids like CR_1001) are preserved.
_EMPHASIS = re.compile(r"[*~`]")
_ITALIC_OPEN = re.compile(r"(?<![A-Za-z0-9_])_(?=\S)")
_ITALIC_CLOSE = re.compile(r"(?<=\S)_(?![A-Za-z0-9_])")


def _deformat(text: str) -> str:
    text = _EMPHASIS.sub("", text)
    text = _ITALIC_OPEN.sub("", text)
    return _ITALIC_CLOSE.sub("", text)


def _clean(value: str) -> str:
    return value.strip().strip(".,;:—-").strip()[:120]


def _norm_key(key: str) -> str:
    return re.sub(r"\s+", " ", key.strip().lower())


def extract_crew_change(text: Optional[str]) -> Optional[dict[str, str]]:
    """Parse a crew sign-on/off notification into whatever structured fields it
    carries — dynamically.

    Captures any present detail (``crew_member``, ``role``, ``email``,
    ``crew_id``, ``vessel``, ``port``) plus the ``action`` (sign_on / sign_off),
    from both labelled ("Name: …", "Role: …", "Vessel: …") and inline phrasings.
    Returns ``None`` when no concrete detail is found, so ordinary chatter that
    merely mentions "sign off" is left as a plain edge.
    """
    if not text:
        return None
    text = _deformat(text)   # drop Slack mrkdwn emphasis so "_Role:_ …" is parsed
    out: dict[str, str] = {}

    # (a) labelled "Key: Value" lines — dynamic, any aliased label.
    for line in text.splitlines():
        m = _LABEL_LINE.match(line)
        if not m:
            continue
        canon = _LABEL_ALIASES.get(_norm_key(m.group(1)))
        if canon and canon not in out:
            out[canon] = _clean(m.group(2))

    # (b) split "Name (Role)" if the crew_member value carries the role inline.
    if "crew_member" in out and "role" not in out:
        nr = _NAME_RANK.search(out["crew_member"])
        if nr:
            out["crew_member"] = _clean(nr.group(1))
            out["role"] = _clean(nr.group(2))

    # (c) inline fallbacks for unlabelled notifications.
    if "crew_member" not in out:
        nr = _NAME_RANK.search(text)
        if nr:
            out["crew_member"] = _clean(nr.group(1))
            out.setdefault("role", _clean(nr.group(2)))
    if "vessel" not in out:
        vi = _VESSEL_INLINE.search(text)
        if vi:
            out["vessel"] = _clean(vi.group(1))
    if "port" not in out:
        pi = _PORT_INLINE.search(text)
        if pi:
            out["port"] = _clean(pi.group(1))
    if "email" not in out:
        em = _EMAIL_INLINE.search(text)
        if em:
            out["email"] = em.group(0)
    if "crew_id" not in out:
        ci = _CREW_ID_INLINE.search(text)
        if ci:
            out["crew_id"] = ci.group(1).upper()

    if not any(k in out for k in _DETAIL_KEYS):
        return None  # no concrete details — leave it as plain chatter

    action = next((a for a, rx in _ACTION_PATTERNS if rx.search(text)), None)
    return {**({"action": action} if action else {}), **out}


class L2JsonlStore:
    """Append-only JSONL store of projected L2 records."""

    def __init__(self, path: str = "./data/l2_store.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # truncate at startup so each run begins with a clean L2 store
        self._fh = self.path.open("w", encoding="utf-8")
        self.total = 0
        self.by_kind: Counter = Counter()
        self.by_label: Counter = Counter()
        self.signoff = 0
        self.recent: deque[dict] = deque(maxlen=100)

    @staticmethod
    def project(event: SignalEvent) -> dict[str, Any]:
        """Pure SignalEvent → L2 record (node / edge / signoff_event)."""
        ss = event.source_system.value
        d = event.data or {}
        rid = event.dedup_id[:12]
        base = {"source_system": ss, "key": event.key, "ts": event.timestamp.isoformat()}

        # crew sign-on/off details parsed from the message body or e-mail subject,
        # surfaced into props (mirrors the ERP crew row's fields).
        crew = extract_crew_change(d.get("text") or d.get("subject"))

        if (event.metadata or {}).get("l2Intent") == "CREATE_SIGNOFF_EVENT":
            props = {"subject": d.get("subject"), "from": d.get("from"),
                     "thread_id": d.get("thread_id")}
            if crew:
                props.update(crew)
            return {**base, "id": f"signoff:{rid}", "kind": "signoff_event",
                    "label": "SignOffEvent", "props": props}
        if ss == "SLACK":
            # channel resolved to its human name by the connector when a bot token
            # is configured; fall back to the raw id. channel_id is always kept.
            props = {"user": d.get("user_name") or d.get("user"),
                     "channel": d.get("channel_name") or d.get("channel"),
                     "channel_id": d.get("channel")}
            if crew:
                props.update(crew)
            return {**base, "id": f"edge:{rid}", "kind": "edge",
                    "label": _SLACK_LABEL.get(event.entity, "SLACK"), "props": props}
        # e-mail family (EMAIL / GMAIL / OUTLOOK) → sender↔recipient edge
        if ss in {"EMAIL", "GMAIL", "OUTLOOK"}:
            props = {"from": d.get("from"), "to": d.get("to"), "subject": d.get("subject")}
            if crew:
                props.update(crew)
            return {**base, "id": f"edge:{rid}", "kind": "edge", "label": "EMAILED",
                    "props": props}
        return {**base, "id": f"node:{rid}", "kind": "node",
                "label": _ERP_LABEL.get(event.entity, event.entity), "props": d}

    def append(self, event: SignalEvent) -> dict[str, Any]:
        """Project + persist one event. Returns the L2 record (for the demo trace)."""
        rec = self.project(event)
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()
        self.total += 1
        self.by_kind[rec["kind"]] += 1
        self.by_label[rec["label"]] += 1
        if rec["kind"] == "signoff_event":
            self.signoff += 1
        self.recent.append(rec)
        return rec

    @property
    def count(self) -> int:
        return self.total

    def counts(self) -> dict[str, Any]:
        return {"total": self.total, "by_kind": dict(self.by_kind),
                "by_label": dict(sorted(self.by_label.items())), "signoff": self.signoff}

    def close(self) -> None:
        self._fh.close()
