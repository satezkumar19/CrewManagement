# Demo dataset & streaming

A large, coherent, **streamable** maritime crew-operations dataset for the L1
SignalFabric demo — the analogue of the Freight-invoice `demo_data` + simulator,
in *our* project context (Slack / Gmail / ERP → OrgMap + SignOffEvent).

## Generate

```bash
make seed           # -> ./data  (python -m demo.seed --out ./data)
```

Produces (~4,600 events, deterministic with `--seed 42`):

| File | What |
|------|------|
| `data/backlog.jsonl` | historical raw events **≤ anchor** — the "lots of history" burst (~3,900) |
| `data/timeline_future.jsonl` | raw events **> anchor** — the live replay runway (~690) |
| `data/entities.json` | vessels, ports, crew (280), Slack channels/users (288) |
| `data/seed_meta.json` | anchor, window, config, per-source/-kind counts |

**Anchor** = the Day-1 demo date `2026-06-08`. Each line is an envelope:

```json
{"occurred_at":"…","source":"slack|email|erp","kind":"…","raw":{ …connector-native payload… }}
```

`raw` is exactly what each connector ingests:
- `slack` → a Slack Events API `event_callback` (message / reaction_added / member_joined_channel)
- `email` → Gmail-style **metadata** record (from/to/cc/subject/thread/sent_at; **no body**), incl. `crew/sign-off`
- `erp`  → a transactional-outbox change row (`crew` / `contract` / `vessel_port`), with a monotonic `seq`

Coherence: crew changes are emitted as **multi-source clusters** — ERP `sign_off_due`
→ sign-off email → `#crew-changes` Slack post → ERP `signed_off` → contract
`completed` → reliever onboarding — so the demo tells one story across all sources.

## Stream

```bash
make stream                 # backlog drain (fast burst, proves idempotency)
make stream-live            # live virtual-clock replay (SPEED=6000 default)
# or:
python -m demo.stream --mode live --speed 8000 --max-seconds 20
```

Both modes route each raw event through the **real** connectors
(`SlackConnector.ingest`, `ErpConnector` outbox + watermark) and publish canonical
`SignalEvent`s to an `EventBus` (default the Day-1 `LoggingEventBus`; pass
Sruthy's `InMemoryBus` to drive L2). Email uses `demo/email_normalize.py`, a
labelled **stand-in for the Day-3 Gmail connector**.

- **backlog** drains everything, then re-runs the dedup-capable connectors → `second_pass_new == 0` (ERP watermark + Slack `event_id` idempotency).
- **live** advances a virtual clock (`speed×`) and replays the future runway in
  time order — connectors keep surfacing NEW signals continuously (streaming, not batch).

Sign-off emails carry `metadata.l2Intent = CREATE_SIGNOFF_EVENT`; the count is
reported as `signoff_intents` (these become SignOffEvent nodes in L2).

> `data/` is wiped and rewritten on every `make seed`, so this README lives in
> `demo/`, not in `data/`.
