"""Demo 1 (headless) — prove the whole pipe against a running server.

    make up && make demo

Checks /healthz (green), then POSTs /demo/inject to push one mock Slack message
and one mock sign-off email through ingress → normalizer → bus → L2 store → SSE,
and prints the per-stage trace. The same events scroll live on the dashboard at
http://localhost:8001/.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = os.getenv("L1_URL", "http://localhost:8001")


def _short(obj, n=160):
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    try:
        h = httpx.get(f"{URL}/healthz", timeout=4).json()
    except Exception as e:  # noqa: BLE001
        print(f"✗ server not reachable at {URL} — run `make up` first ({e})")
        return 1

    ok = h.get("status") == "ok"
    print(f"{'✓' if ok else '✗'} health: {h.get('status')} · "
          f"connectors={[c['name'] for c in h.get('connectors', [])]} · "
          f"L2 records={h.get('l2_records')}")
    if not ok:
        return 1

    r = httpx.post(f"{URL}/demo/inject", timeout=10).json()
    if "error" in r:
        print("✗", r["error"])
        return 1

    ctx = r.get("context", {})
    print(f"\n▶ injected {r['injected']} events for {ctx.get('crew')} "
          f"({ctx.get('rank')}) — {ctx.get('vessel')} at {ctx.get('port')}\n")

    for item in r["trace"]:
        norm, l2 = item["normalized"], item["l2"]
        print(f"── {item['source'].upper()} ──────────────────────────────")
        print(f"  ① ingress    raw: {_short(item['raw'])}")
        print(f"  ② normalized {norm['source_system']}/{norm['entity']} key={norm['key']}")
        print(f"  ④ L2 record  {l2['kind']}:{l2['label']}"
              + ("   ⟵ SignOffEvent" if l2['kind'] == 'signoff_event' else ""))
        print()

    store = r.get("l2_store") or {}
    print(f"✓ L2 store now holds {store.get('total')} records "
          f"({store.get('signoff')} SignOffEvent). Watch them scroll at {URL}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
