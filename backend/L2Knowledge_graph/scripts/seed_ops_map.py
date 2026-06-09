"""
Seed / inspect the L2 OpsMap dimension (process mining over crew-change workflows).

OpsMap mines the events the running workflow emits, so in normal operation it
populates itself as sign-off/sign-on workflows run — no seeding required. This
script exists for two situations:

  1. DEMO before any live runs — replay a handful of CAPTURED SAMPLE TRACES (the
     exact event_type sequences WorkflowService emits for the happy path, a
     compliance rejection, and a failure) so the OpsMap API / UI has a populated,
     realistic process graph to render immediately.  ->  --demo

  2. AGE PERSISTENCE — once cases have been mined (live or via --demo), write the
     mined directly-follows model into the `maritime` graph as
     (:Activity)-[:NEXT]->(:Activity) edges so the process model is queryable in
     Cypher alongside EntityMap.  ->  --persist   (requires GRAPH_BACKEND=age)

Usage:
    python -m L2Knowledge_graph.scripts.seed_ops_map --demo
    python -m L2Knowledge_graph.scripts.seed_ops_map --demo --persist
    python -m L2Knowledge_graph.scripts.seed_ops_map            # just print the model

NOTE: the event log is in-memory per process. Running this script seeds the log in
THIS process only (useful for offline inspection / AGE persistence). To populate the
log inside the live API process, run real workflows or POST a replay there.
"""
import argparse
import asyncio
import json

from L2Knowledge_graph import ops_map

# Captured sample traces — each is one crew-change CASE expressed as the ordered
# (event_type, agent_name, ts_iso) events WorkflowService actually emits. Timestamps
# are spaced to produce realistic per-handoff durations (and a deliberate bottleneck
# at Sign-Off Confirmed -> Compliance Check in the rejection case).
SAMPLE_TRACES = {
    # Clean happy path — matched, travel + notify done, signed off, compliance clears.
    "demo-case-1": [
        ("workflow_created", "Master Agent", "2026-06-08T08:00:00"),
        ("agent_completed", "Crew Matching Agent", "2026-06-08T08:00:30"),
        ("agent_completed", "Travel Agent", "2026-06-08T08:00:45"),
        ("agent_completed", "Notification Agent", "2026-06-08T08:00:55"),
        ("crew_updated", "Master Agent", "2026-06-08T08:01:10"),
        ("auto_compliance", "Master Agent", "2026-06-08T08:01:20"),
        ("crew_signed_on", "Compliance Agent", "2026-06-08T08:02:00"),
    ],
    # Second happy path, slightly different specialist interleaving + slower travel.
    "demo-case-2": [
        ("workflow_created", "Master Agent", "2026-06-08T09:00:00"),
        ("agent_completed", "Crew Matching Agent", "2026-06-08T09:00:40"),
        ("agent_completed", "Notification Agent", "2026-06-08T09:00:50"),
        ("agent_completed", "Travel Agent", "2026-06-08T09:01:30"),
        ("crew_updated", "Master Agent", "2026-06-08T09:01:45"),
        ("auto_compliance", "Master Agent", "2026-06-08T09:01:55"),
        ("crew_signed_on", "Compliance Agent", "2026-06-08T09:02:30"),
    ],
    # Compliance REJECTION — long wait before compliance, then rejected (bottleneck).
    "demo-case-3": [
        ("workflow_created", "Master Agent", "2026-06-08T10:00:00"),
        ("agent_completed", "Crew Matching Agent", "2026-06-08T10:00:35"),
        ("agent_completed", "Travel Agent", "2026-06-08T10:00:50"),
        ("agent_completed", "Notification Agent", "2026-06-08T10:01:05"),
        ("crew_updated", "Master Agent", "2026-06-08T10:01:20"),
        ("auto_compliance", "Master Agent", "2026-06-08T10:05:00"),  # 3m40s wait
        ("sign_on_rejected", "Compliance Agent", "2026-06-08T10:06:00"),
    ],
    # FAILURE — workflow errored after matching.
    "demo-case-4": [
        ("workflow_created", "Master Agent", "2026-06-08T11:00:00"),
        ("agent_completed", "Crew Matching Agent", "2026-06-08T11:00:30"),
        ("workflow_failed", "Master Agent", "2026-06-08T11:00:40"),
    ],
}


def seed_demo() -> None:
    ops_map.reset_event_log()
    cases = 0
    for case_id, steps in SAMPLE_TRACES.items():
        ops_map.record_trace(case_id, [(et, an, ts) for (et, an, ts) in steps])
        cases += 1
    print(f"Seeded {cases} captured sample crew-change traces into the OpsMap event log.\n")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed / inspect the L2 OpsMap dimension.")
    ap.add_argument("--demo", action="store_true", help="replay captured sample traces")
    ap.add_argument("--persist", action="store_true", help="write mined model into AGE (needs GRAPH_BACKEND=age)")
    args = ap.parse_args()

    if args.demo:
        seed_demo()

    print("── OpsMap summary ──")
    print(json.dumps(ops_map.ops_map_summary(), indent=2))
    print("\n── Variants ──")
    print(json.dumps(ops_map.process_variants(), indent=2))
    print("\n── Bottlenecks ──")
    print(json.dumps(ops_map.bottlenecks(), indent=2))
    print("\n── Conformance ──")
    print(json.dumps(ops_map.conformance(), indent=2))

    if args.persist:
        print("\n── Persisting model to AGE ──")
        result = await ops_map.persist_process_model()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
