"""Streamable demo driver — replays the seeded dataset through the real pipeline.

Two modes, mirroring the Freight-invoice demo:

  backlog  — drain ``data/backlog.jsonl`` through the connectors as fast as
             possible (the "lots of history" burst). Runs a second pass to prove
             idempotency (0 new the second time).

  live     — advance a virtual clock and replay ``data/timeline_future.jsonl`` in
             time order, sped up by ``--speed`` (e.g. 6000× → 1 real second ≈
             100 simulated minutes). This is the "world in motion" — connectors
             keep surfacing NEW SignalEvents continuously, never in batch.

Routing by source (each line is ``{occurred_at, source, raw}``):
  slack -> SlackConnector.ingest        (real Day-1 connector)
  erp   -> ErpConnector.ingest          (real Day-1 connector)
  email -> demo email_normalize         (stand-in for the Day-3 Gmail connector)

Every normalized SignalEvent is published to an EventBus (default the Day-1
LoggingEventBus placeholder; pass Sruthy's InMemoryBus to drive L2).

Run (after `python -m demo.seed`):
    python -m demo.stream --mode backlog
    python -m demo.stream --mode live --speed 8000 --max-seconds 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from connectors.erp import ErpConnector, InMemoryOutboxAdapter
from connectors.slack import SlackConnector
from core.bus import EventBus, LoggingEventBus
from core.signal import SignalEvent
from demo.email_normalize import email_to_signal


class DemoStreamer:
    """Routes raw demo events to connectors and publishes SignalEvents to a bus."""

    def __init__(self, data_dir: str, *, tenant_id: str = "maritime-acme",
                 bus: Optional[EventBus] = None) -> None:
        self.data = Path(data_dir)
        self.tenant_id = tenant_id
        self.bus = bus or LoggingEventBus()

        meta = json.loads((self.data / "seed_meta.json").read_text(encoding="utf-8"))
        self.anchor = datetime.fromisoformat(meta["anchor"])

        self.slack = SlackConnector(tenant_id=tenant_id)
        self._erp_outbox = InMemoryOutboxAdapter()
        self.erp = ErpConnector(tenant_id=tenant_id, adapter=self._erp_outbox)

        # running tallies
        self.by_source: Counter = Counter()      # by SignalEvent.source_system
        self.by_entity: Counter = Counter()
        self.signoff_events = 0

    # ----------------------------------------------------------- normalization
    async def _route(self, line: dict[str, Any]) -> list[SignalEvent]:
        source, raw = line["source"], line["raw"]
        if source == "slack":
            return await self.slack.ingest(raw)
        if source == "erp":
            return await self.erp.ingest(raw)
        if source == "email":
            return email_to_signal(raw, self.tenant_id)
        return []

    def _note_ingress(self, source: str, n: int = 1) -> None:
        if hasattr(self.bus, "note_ingress"):
            self.bus.note_ingress(source, n)

    async def _emit(self, events: list[SignalEvent],
                    raw: Optional[dict[str, Any]] = None) -> int:
        for ev in events:
            if raw is not None:
                # Demo provenance: stash the raw ingress payload so the live-tail
                # UI can show raw → normalized → L2 per row. Namespaced + ignored
                # by the L2 projector and dedup; stripped from the normalized view.
                ev.metadata["_ingress_raw"] = raw
            await self.bus.publish(ev)
            self.by_source[ev.source_system.value] += 1
            self.by_entity[ev.entity] += 1
            if ev.metadata.get("l2Intent") == "CREATE_SIGNOFF_EVENT":
                self.signoff_events += 1
        return len(events)

    def _read(self, fname: str) -> list[dict[str, Any]]:
        path = self.data / fname
        out: list[dict[str, Any]] = []
        if not path.exists():
            return out
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln))
        return out

    # ------------------------------------------------------------- backlog mode
    async def run_backlog(self) -> dict:
        lines = self._read("backlog.jsonl")
        slack_lines = [l for l in lines if l["source"] == "slack"]
        email_lines = [l for l in lines if l["source"] == "email"]
        erp_rows = [l["raw"] for l in lines if l["source"] == "erp"]

        # ERP via the outbox adapter + watermark: load all change rows, then poll
        # to drain. This is the path that proves 0-loss + idempotent resume.
        for r in erp_rows:
            self._erp_outbox.append(table=r["table"], op=r["op"],
                                    occurred_at=r["occurred_at"], data=r["data"])
        self._note_ingress("erp", len(erp_rows))
        total = await self._emit(await self.erp.poll())

        # Slack + Email inline
        for ln in slack_lines:
            self._note_ingress("slack")
            total += await self._emit(await self.slack.ingest(ln["raw"]), raw=ln["raw"])
        for ln in email_lines:
            self._note_ingress("email")
            total += await self._emit(email_to_signal(ln["raw"], self.tenant_id), raw=ln["raw"])

        # Idempotency second pass over the dedup-capable connectors:
        #   ERP poll → 0 (watermark advanced) · Slack ingest → 0 (event_id seen)
        second = len(await self.erp.poll())
        for ln in slack_lines:
            second += len(await self.slack.ingest(ln["raw"]))

        return {
            "mode": "backlog",
            "raw_lines": len(lines),
            "signals_emitted": total,
            "second_pass_new": second,   # 0 = idempotent
            "by_source_system": dict(self.by_source),
            "by_entity": dict(sorted(self.by_entity.items())),
            "signoff_intents": self.signoff_events,
        }

    # --------------------------------------------------------------- live mode
    async def run_live(self, *, speed: float = 6000.0, tick: float = 0.4,
                       max_seconds: Optional[float] = None,
                       on_tick=None) -> dict:
        future = sorted(self._read("timeline_future.jsonl"),
                        key=lambda d: d["occurred_at"])
        idx, applied = 0, 0
        start_real = time.monotonic()
        while idx < len(future):
            elapsed = time.monotonic() - start_real
            virtual_now = self.anchor + timedelta(seconds=elapsed * speed)
            while idx < len(future) and \
                    datetime.fromisoformat(future[idx]["occurred_at"]) <= virtual_now:
                self._note_ingress(future[idx]["source"])
                applied += await self._emit(await self._route(future[idx]), raw=future[idx]["raw"])
                idx += 1
            if on_tick:
                on_tick(virtual_now, idx, len(future), self.by_source)
            if max_seconds is not None and elapsed >= max_seconds:
                break
            if idx < len(future):
                await asyncio.sleep(tick)
        return {
            "mode": "live",
            "future_total": len(future),
            "replayed": idx,
            "signals_emitted": applied,
            "by_source_system": dict(self.by_source),
            "by_entity": dict(sorted(self.by_entity.items())),
            "signoff_intents": self.signoff_events,
        }


def _print_tick(vn: datetime, idx: int, total: int, by_source: Counter) -> None:
    bar = " ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
    print(f"  [{vn:%Y-%m-%d %H:%M}] replayed {idx}/{total}  | {bar}", flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Stream the L1 SignalFabric demo dataset")
    ap.add_argument("--data", default="./data")
    ap.add_argument("--mode", choices=["backlog", "live"], default="backlog")
    ap.add_argument("--speed", type=float, default=6000.0, help="live virtual-clock multiplier")
    ap.add_argument("--tick", type=float, default=0.4, help="live wall-clock step (s)")
    ap.add_argument("--max-seconds", type=float, default=None, help="live: stop after N real seconds")
    args = ap.parse_args(argv)

    streamer = DemoStreamer(args.data)
    if args.mode == "backlog":
        result = asyncio.run(streamer.run_backlog())
    else:
        print(f"Live replay @ {args.speed}× (Ctrl-C to stop)…")
        result = asyncio.run(streamer.run_live(
            speed=args.speed, tick=args.tick, max_seconds=args.max_seconds,
            on_tick=_print_tick))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
