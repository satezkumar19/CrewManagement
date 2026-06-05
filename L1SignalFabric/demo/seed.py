"""Seed CLI — generate the demo dataset into ``data/``.

Splits the generated world at an ``anchor`` timestamp (default the Day-1 demo
date, 2026-06-08):

  * backlog  (<= anchor)  -> data/backlog.jsonl        (lots of history)
  * future   (> anchor)   -> data/timeline_future.jsonl (live replay runway)

Also writes ``entities.json`` (vessels/ports/crew/channels/users) and
``seed_meta.json`` (anchor + counts). ERP events get a monotonic ``seq`` stamped
in global time order across the whole stream — the transactional-outbox property
the ERP connector's watermark relies on.

Run:
    python -m demo.seed --out ./data
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .generator import GenConfig, WorldGenerator, WorldEvent

DEFAULT_ANCHOR = datetime(2026, 6, 8, tzinfo=timezone.utc)


def _stamp_erp_seq(events: list[WorldEvent]) -> None:
    """Assign monotonic outbox sequence numbers to ERP events in time order."""
    seq = 0
    for ev in events:  # events arrive pre-sorted by occurred_at
        if ev.source == "erp":
            seq += 1
            ev.raw["seq"] = seq


def _counts(events: list[WorldEvent]) -> dict:
    by_source = Counter(e.source for e in events)
    by_kind = Counter(e.kind for e in events)
    return {"by_source": dict(by_source), "by_kind": dict(sorted(by_kind.items()))}


def seed(out_dir: str, config: GenConfig, anchor: datetime) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    gen = WorldGenerator(anchor=anchor, config=config)
    events = gen.generate()
    _stamp_erp_seq(events)

    backlog = [e for e in events if e.occurred_at <= anchor]
    future = [e for e in events if e.occurred_at > anchor]

    with (out / "backlog.jsonl").open("w", encoding="utf-8") as fh:
        for e in backlog:
            fh.write(json.dumps(e.to_json()) + "\n")
    with (out / "timeline_future.jsonl").open("w", encoding="utf-8") as fh:
        for e in future:
            fh.write(json.dumps(e.to_json()) + "\n")

    (out / "entities.json").write_text(json.dumps(gen.entities(), indent=2), encoding="utf-8")

    meta = {
        "anchor": anchor.isoformat(),
        "window": {"start": gen.start.isoformat(), "end": gen.end.isoformat()},
        "config": vars(config),
        "total_events": len(events),
        "backlog_events": len(backlog),
        "future_events": len(future),
        "entities": {
            "vessels": len(gen.vessels), "ports": len(gen.ports),
            "crew": len(gen.crew), "channels": len(gen.channels),
            "users": len(gen.users),
        },
        "backlog_breakdown": _counts(backlog),
        "future_breakdown": _counts(future),
    }
    (out / "seed_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv: Iterable[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Seed the L1 SignalFabric demo dataset")
    ap.add_argument("--out", default="./data")
    ap.add_argument("--vessels", type=int, default=20)
    ap.add_argument("--crew-per-vessel", type=int, default=14)
    ap.add_argument("--crew-changes", type=int, default=160)
    ap.add_argument("--ambient-slack", type=int, default=1500)
    ap.add_argument("--routine-emails", type=int, default=420)
    ap.add_argument("--weeks-back", type=int, default=6)
    ap.add_argument("--weeks-forward", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--anchor", default=None, help="ISO datetime; default 2026-06-08T00:00:00Z")
    args = ap.parse_args(list(argv) if argv is not None else None)

    anchor = DEFAULT_ANCHOR
    if args.anchor:
        anchor = datetime.fromisoformat(args.anchor)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

    cfg = GenConfig(
        num_vessels=args.vessels, crew_per_vessel=args.crew_per_vessel,
        crew_changes=args.crew_changes, ambient_slack=args.ambient_slack,
        routine_emails=args.routine_emails, weeks_back=args.weeks_back,
        weeks_forward=args.weeks_forward, seed=args.seed,
    )
    meta = seed(args.out, cfg, anchor)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
