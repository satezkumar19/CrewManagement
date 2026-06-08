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
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional

from core.signal import SignalEvent

# source/entity → L2 edge label (OrgMap tribal-knowledge edges)
_SLACK_LABEL = {"message": "POSTED_IN", "reaction": "REACTED_IN", "channel_join": "MEMBER_OF"}
_ERP_LABEL = {"crew": "Crew", "contract": "Contract", "vessel_port": "Vessel"}


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

        if (event.metadata or {}).get("l2Intent") == "CREATE_SIGNOFF_EVENT":
            return {**base, "id": f"signoff:{rid}", "kind": "signoff_event",
                    "label": "SignOffEvent",
                    "props": {"subject": d.get("subject"), "from": d.get("from"),
                              "thread_id": d.get("thread_id")}}
        if ss == "SLACK":
            return {**base, "id": f"edge:{rid}", "kind": "edge",
                    "label": _SLACK_LABEL.get(event.entity, "SLACK"),
                    "props": {"user": d.get("user"), "channel": d.get("channel")}}
        # e-mail family (EMAIL / GMAIL / OUTLOOK) → sender↔recipient edge
        if ss in {"EMAIL", "GMAIL", "OUTLOOK"}:
            return {**base, "id": f"edge:{rid}", "kind": "edge", "label": "EMAILED",
                    "props": {"from": d.get("from"), "to": d.get("to"),
                              "subject": d.get("subject")}}
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
