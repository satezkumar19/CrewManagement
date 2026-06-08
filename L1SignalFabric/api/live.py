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
import logging
import os
import random
import subprocess
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from core.bus import DeadLetterQueue, InMemoryBus
from core.signal import SignalEvent
from demo.email_normalize import email_to_signal

logger = logging.getLogger("signalfabric.api.live")

_STATIC = Path(__file__).parent / "static"

# A sink turns one published event into a downstream L2 record (or None).
L2Sink = Callable[[SignalEvent], Optional[dict]]


class BroadcastBus:
    """EventBus that runs an L2 sink, fans out to SSE clients, and tracks the
    per-stage counters the pipeline dashboard renders."""

    def __init__(self, keep_last: int = 200, queue_max: int = 5000) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._sink: Optional[L2Sink] = None
        # A real InMemoryBus tapped on every publish purely to surface its
        # per-ingress console log (PUBLISH / DUP-DROP) in the UI. It has no
        # subscribers here — the L2 sink stays wired via set_sink — so it adds
        # the bus's idempotency view alongside the viewer stream.
        self.inmem = InMemoryBus()
        self.dlq = DeadLetterQueue()     # capture L2-sink failures (isolated, recorded)
        self.recent: deque[dict] = deque(maxlen=keep_last)
        self.recent_buslog: deque[dict] = deque(maxlen=keep_last)
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
            try:                                  # a failing sink is isolated + dead-lettered,
                l2rec = self._sink(event)         # never breaking the live stream
                if l2rec is not None:
                    self.l2 += 1
            except Exception as exc:
                self.dlq.add(event, exc, subscriber="l2_sink")
                logger.exception("[live] L2 sink failed for %s (dead-lettered)", event.event_id)

        payload = self._payload(event, l2rec, is_signoff)
        self.recent.append(payload)
        self._fanout(payload)

        # tap the InMemoryBus so its per-ingress console log is visible in the UI
        await self.inmem.publish(event)
        if self.inmem.last_log_line is not None:
            logline = {"type": "buslog", **self.inmem.last_log_line}
            self.recent_buslog.append(self.inmem.last_log_line)
            self._fanout(logline)
        return l2rec

    def _fanout(self, payload: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow client: drop the line; totals stay authoritative

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
        stats = self.inmem.stats()
        stats["dead_letters"] = self.dlq.count   # surface the live sink DLQ in the UI
        return {"type": "snapshot", "totals": self.totals(),
                "recent": list(self.recent)[-60:],
                "buslog": list(self.recent_buslog)[-80:],
                "bus_stats": stats}

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
        # Full three-stage trace per row, for the live-tail detail drawer:
        #   raw (ingress)  → normalized (SignalEvent)  → l2_record
        # The raw is demo provenance carried in metadata._ingress_raw; pop it out
        # so the normalized view stays the clean canonical event.
        normalized = ev.model_dump(mode="json")
        raw = None
        if isinstance(normalized.get("metadata"), dict):
            raw = normalized["metadata"].pop("_ingress_raw", None)
        return {
            "type": "signal",
            "source": ev.source_system.value,
            "entity": ev.entity,
            "key": ev.key,
            "summary": self._summary(ev),
            "signoff": is_signoff,
            "l2": {"kind": l2rec.get("kind"), "label": l2rec.get("label")} if l2rec else None,
            "raw": raw,
            "normalized": normalized,
            "l2_record": l2rec,
            "ts": ev.timestamp.isoformat(),
            "totals": self.totals(),
        }


router = APIRouter(tags=["live"])


@router.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(_STATIC / "dashboard.html")


@router.get("/bus/log")
async def bus_log(request: Request, n: int = 200) -> dict:
    """Recent InMemoryBus console log (one line per ingress) + bus stats.

    The live tail streams these lines over SSE as `type:"buslog"` events; this
    endpoint backfills them for a fresh load / non-SSE clients."""
    bus = request.app.state.bus
    inmem = getattr(bus, "inmem", None)
    if inmem is None:
        return {"lines": [], "stats": None}
    return {"lines": inmem.recent_log(n), "stats": inmem.stats()}


@router.get("/orgmap")
async def orgmap(request: Request, nodes: int = 300, edges: int = 600) -> dict:
    """Snapshot of the live OrgMap knowledge graph (nodes + edges + label stats),
    upserted from every projected L2 record. Drives the OrgMap viewer tab."""
    om = getattr(request.app.state, "orgmap", None)
    if om is None:
        return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0}}
    return om.snapshot(limit_nodes=nodes, limit_edges=edges)


@router.get("/bus/dlq")
async def bus_dlq(request: Request, n: int = 100) -> dict:
    """Dead-letter queue: events whose L2 sink raised (isolated + recorded)."""
    bus = request.app.state.bus
    dlq = getattr(bus, "dlq", None)
    if dlq is None:
        return {"count": 0, "items": []}
    return {"count": dlq.count, "items": dlq.recent(n)}


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
    """Build ONE mock Slack message + ONE mock sign-off email + ONE ERP crew-DB
    change row from the generated entities (falls back to literals if no dataset
    is present). All three describe the *same* crew change so Demo 1 tells one
    story across Slack (tribal knowledge), Email (sign-off), and ERP (system of
    record)."""
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
    erp = {
        "table": "crew",
        "op": "update",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "seq": 900000 + seq,   # high range so it never collides with dataset seqs
        "data": {
            "crew_id": c.get("crew_id", "CR-0001"),
            "name": name,
            "rank": rank,
            "vessel": vessel,
            "status": "Signing Off",
            "relief_port": port,
        },
    }
    return {"slack": slack, "email": email, "erp": erp,
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
        ev.metadata["_ingress_raw"] = mock["slack"]   # provenance for the live-tail drawer
        l2 = await bus.publish(ev)
        trace.append({"source": "slack", "raw": mock["slack"],
                      "normalized": ev.model_dump(mode="json"), "l2": l2})

    # --- Email: ingress → normalizer → bus → L2 (sign-off → SignOffEvent) ---
    if hasattr(bus, "note_ingress"):
        bus.note_ingress("email")
    for ev in email_to_signal(mock["email"], tenant):
        ev.metadata["_ingress_raw"] = mock["email"]   # provenance for the live-tail drawer
        l2 = await bus.publish(ev)
        trace.append({"source": "email", "raw": mock["email"],
                      "normalized": ev.model_dump(mode="json"), "l2": l2})

    # --- ERP: one Crew-DB outbox change row → connector(normalize) → bus → L2 node ---
    if hasattr(bus, "note_ingress"):
        bus.note_ingress("erp")
    for ev in await state.erp.ingest(mock["erp"]):
        ev.metadata["_ingress_raw"] = mock["erp"]
        l2 = await bus.publish(ev)
        trace.append({"source": "erp", "raw": mock["erp"],
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


# ------------------------------ test / critic agents ------------------------------ #
# Run the tests/agents/* harness from the dashboard. Each agent is launched as an
# isolated subprocess (same interpreter, project root as cwd) so it can't mutate
# this server's logging/stdout — its captured text output is returned verbatim.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_agent_blocking(module: str) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # the agents print box-drawing/Unicode
    proc = subprocess.run(
        [sys.executable, "-m", module],
        cwd=str(_PROJECT_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    return {"module": module, "exit_code": proc.returncode,
            "output": (proc.stdout or "") + (proc.stderr or "")}


async def _run_agent_module(module: str) -> dict:
    # Offload to a thread: uvicorn's Windows event loop has no async-subprocess
    # support, so we run the agent with a blocking subprocess.run off the loop.
    try:
        return await asyncio.to_thread(_run_agent_blocking, module)
    except Exception as exc:  # surface launch failures in the panel instead of 500ing
        return {"module": module, "exit_code": -1,
                "error": f"failed to launch {module}: {exc!r}"}


@router.post("/agents/test")
async def agents_test() -> dict:
    """Run the Test Agent (every scenario, PASS/FAIL each) and return its output."""
    return await _run_agent_module("tests.agents.test_agent")


@router.post("/agents/critic")
async def agents_critic() -> dict:
    """Run the Critic Agent (contract validation + critique) and return its output."""
    return await _run_agent_module("tests.agents.critic_agent")
