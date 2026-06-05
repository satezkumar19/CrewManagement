"""Live view: an SSE-broadcasting EventBus + a browser dashboard that shows the
**whole pipe** — ingress → normalizer → bus → L2 store → live tail.

`BroadcastBus` implements the `core.EventBus` Protocol the connectors publish to,
and additionally:
  * counts **ingress** (raw events received) via `note_ingress`,
  * runs an L2 **sink** on every publish (projecting into the L2 JSONL store),
  * fans every event out to Server-Sent-Events clients with per-stage totals,
  * keeps authoritative running totals + a recent buffer.

It is a viewer bus (no durability/ordering/Redis) — swappable for Sruthy's
`InMemoryBus`. The `/demo/*` routes drive the replay / a single injection inside
the server process so the dashboard shows the pipe in motion.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from core.signal import SignalEvent
from demo.email_normalize import email_to_signal

_STATIC = Path(__file__).parent / "static"

# A sink turns one published event into a downstream L2 record (or None).
L2Sink = Callable[[SignalEvent], Optional[dict]]


class BroadcastBus:
    """EventBus that runs an L2 sink, fans out to SSE clients, and tracks the
    per-stage counters the pipeline dashboard renders."""

    def __init__(self, keep_last: int = 200, queue_max: int = 5000) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._sink: Optional[L2Sink] = None
        self.recent: deque[dict] = deque(maxlen=keep_last)
        # stage counters
        self.ingress = 0                 # raw events received (ingress stage)
        self.ingress_by_source: Counter = Counter()
        self.total = 0                   # normalized + published (normalizer/bus stage)
        self.l2 = 0                      # records written to the L2 store
        self.signoff = 0                 # SignOffEvent nodes created in L2
        self.by_source: Counter = Counter()
        self.by_entity: Counter = Counter()
        self._queue_max = queue_max

    # --- wiring ---
    def set_sink(self, sink: L2Sink) -> None:
        self._sink = sink

    def note_ingress(self, source: str, n: int = 1) -> None:
        """Record that `n` raw events entered the pipe from `source`."""
        self.ingress += n
        self.ingress_by_source[source] += n

    # --- EventBus Protocol ---
    async def publish(self, event: SignalEvent) -> Optional[dict]:
        self.total += 1
        self.by_source[event.source_system.value] += 1
        self.by_entity[event.entity] += 1
        is_signoff = (event.metadata or {}).get("l2Intent") == "CREATE_SIGNOFF_EVENT"
        if is_signoff:
            self.signoff += 1

        l2rec: Optional[dict] = None
        if self._sink is not None:
            l2rec = self._sink(event)
            if l2rec is not None:
                self.l2 += 1

        payload = self._payload(event, l2rec, is_signoff)
        self.recent.append(payload)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow client: drop the line; totals stay authoritative
        return l2rec

    @property
    def count(self) -> int:
        return self.total

    # --- SSE plumbing ---
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_max)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def totals(self) -> dict[str, Any]:
        return {
            "ingress": self.ingress,
            "normalized": self.total,   # connector mapper output == published
            "bus": self.total,
            "l2": self.l2,
            "signoff": self.signoff,
            "total": self.total,        # back-compat alias
            "by_source": dict(self.by_source),
            "by_entity": dict(self.by_entity),
        }

    def snapshot(self) -> dict[str, Any]:
        return {"type": "snapshot", "totals": self.totals(),
                "recent": list(self.recent)[-60:]}

    @staticmethod
    def _summary(ev: SignalEvent) -> str:
        d = ev.data or {}
        if "text" in d:
            return str(d["text"])[:120]
        if "subject" in d:
            return str(d["subject"])[:120]
        if ev.entity == "reaction":
            return f":{d.get('reaction', '?')}: in {d.get('channel', '')}"
        if ev.entity == "channel_join":
            return f"{d.get('user', '?')} joined {d.get('channel', '')}"
        op = (ev.metadata or {}).get("op", "")
        return f"{op} {ev.entity} {ev.key}".strip()

    def _payload(self, ev: SignalEvent, l2rec: Optional[dict], is_signoff: bool) -> dict[str, Any]:
        return {
            "type": "signal",
            "source": ev.source_system.value,
            "entity": ev.entity,
            "key": ev.key,
            "summary": self._summary(ev),
            "signoff": is_signoff,
            "l2": {"kind": l2rec.get("kind"), "label": l2rec.get("label")} if l2rec else None,
            "ts": ev.timestamp.isoformat(),
            "totals": self.totals(),
        }


router = APIRouter(tags=["live"])


@router.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(_STATIC / "dashboard.html")


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    bus = request.app.state.bus
    if not isinstance(bus, BroadcastBus):
        async def empty():
            yield "event: error\ndata: {\"error\":\"bus is not SSE-capable\"}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    q = bus.subscribe()

    async def gen():
        yield f"event: snapshot\ndata: {json.dumps(bus.snapshot())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------------ mock builders ------------------------------ #
def _load_entities(data_dir: str) -> dict:
    p = Path(data_dir) / "entities.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _mock_pair(ents: dict, seq: int) -> dict:
    """Build ONE mock Slack message + ONE mock sign-off email from the generated
    entities (falls back to literals if no dataset is present)."""
    rnd = random.Random(seq)
    crew = list(ents.get("crew", {}).values()) or [
        {"name": "Arjun Sharma", "rank": "2nd Officer", "vessel": "MV Orion Star",
         "crew_id": "CR-0001"}]
    c = rnd.choice(crew)
    name, rank = c["name"], c["rank"]
    vessel = c.get("vessel", "MV Orion Star")
    port = rnd.choice(ents.get("ports", ["Singapore", "Rotterdam", "Fujairah"]))
    uid = next((u for u, info in ents.get("users", {}).items()
                if info.get("crew_id") == c.get("crew_id")), "U-0001")
    ts = f"{time.time():.0f}.{seq:06d}"
    slack = {
        "type": "event_callback", "event_id": f"Ev-DEMO-{seq:05d}", "team_id": "T-FLEET",
        "event": {"type": "message", "channel": "C-CREW", "user": uid,
                  "text": f"Crew change: {name} ({rank}) signing off {vessel} at {port} — reliever inbound.",
                  "ts": ts},
    }
    email = {
        "message_id": f"<demo-{seq:05d}@mail.fleet.example>",
        "thread_id": f"thr-demo-{seq}",
        "from": {"name": "Priya Menon", "address": "priya.menon@fleet.example"},
        "to": [{"name": name, "address": f"{name.lower().replace(' ', '.')}@crew.fleet.example"}],
        "cc": [],
        "subject": f"Sign-Off Notification: {name} ({rank}) — {vessel} at {port}",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "labels": ["crew/sign-off"],
    }
    return {"slack": slack, "email": email,
            "ctx": {"crew": name, "rank": rank, "vessel": vessel, "port": port}}


@router.post("/demo/inject")
async def demo_inject(request: Request, data: str = "./data") -> dict:
    """Demo 1: inject one mock Slack message + one mock sign-off email and push
    them through the REAL pipe (connector/normalizer → bus → L2 store → SSE).
    Returns the full per-stage trace (raw → normalized → L2 record)."""
    state = request.app.state
    seq = getattr(state, "inject_seq", 0) + 1
    state.inject_seq = seq
    mock = _mock_pair(_load_entities(data), seq)

    bus, slack, tenant = state.bus, state.slack, state.tenant_id
    trace: list[dict] = []

    # --- Slack: ingress → connector(normalize) → bus → L2 ---
    if hasattr(bus, "note_ingress"):
        bus.note_ingress("slack")
    for ev in await slack.ingest(mock["slack"]):
        l2 = await bus.publish(ev)
        trace.append({"source": "slack", "raw": mock["slack"],
                      "normalized": ev.model_dump(mode="json"), "l2": l2})

    # --- Email: ingress → normalizer → bus → L2 (sign-off → SignOffEvent) ---
    if hasattr(bus, "note_ingress"):
        bus.note_ingress("email")
    for ev in email_to_signal(mock["email"], tenant):
        l2 = await bus.publish(ev)
        trace.append({"source": "email", "raw": mock["email"],
                      "normalized": ev.model_dump(mode="json"), "l2": l2})

    return {
        "injected": len(trace),
        "context": mock["ctx"],
        "trace": trace,
        "l2_store": state.l2_store.counts() if getattr(state, "l2_store", None) else None,
    }


# ------------------------------ demo replay ------------------------------ #
def _streamer(request: Request, data_dir: str):
    from demo.stream import DemoStreamer
    return DemoStreamer(data_dir, bus=request.app.state.bus)


@router.post("/demo/start")
async def demo_start(request: Request, speed: float = 6000.0, data: str = "./data") -> dict:
    state = request.app.state
    if getattr(state, "demo_task", None) and not state.demo_task.done():
        return {"running": True, "note": "already running"}
    if not (Path(data) / "seed_meta.json").exists():
        return {"error": f"no dataset at {data} — run `make seed` first"}

    streamer = _streamer(request, data)

    async def run():
        try:
            await streamer.run_live(speed=speed, tick=0.3)
        except asyncio.CancelledError:
            pass

    state.demo_task = asyncio.create_task(run())
    return {"started": True, "mode": "live", "speed": speed}


@router.post("/demo/backlog")
async def demo_backlog(request: Request, data: str = "./data") -> dict:
    if not (Path(data) / "seed_meta.json").exists():
        return {"error": f"no dataset at {data} — run `make seed` first"}
    result = await _streamer(request, data).run_backlog()
    return {"loaded": result["signals_emitted"], "by_source": result["by_source_system"]}


@router.post("/demo/stop")
async def demo_stop(request: Request) -> dict:
    t = getattr(request.app.state, "demo_task", None)
    if t and not t.done():
        t.cancel()
        return {"stopped": True}
    return {"stopped": False, "note": "nothing running"}


@router.get("/demo/status")
async def demo_status(request: Request) -> dict:
    t = getattr(request.app.state, "demo_task", None)
    bus = request.app.state.bus
    return {
        "running": bool(t and not t.done()),
        "totals": bus.totals() if isinstance(bus, BroadcastBus) else None,
        "l2_store": request.app.state.l2_store.counts()
        if getattr(request.app.state, "l2_store", None) else None,
    }
