"""
Managed-path smoke test for the L3 Intelligence Supervisor.

Exercises the REAL Claude Managed-Agents flow: the coordinator session delegates to the
3 specialist sub-agents, whose tool calls are resolved back through the existing
investigators, then Python fuses + notifies. Asserts the result is well-formed.

Skips (exit 0) unless INTEL_BACKEND=managed and the L3 coordinator is configured, so it
is safe to run in CI without a key.

Run:
    cd backend
    INTEL_BACKEND=managed python -m scripts.verify_l3_managed
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import structlog  # noqa: E402
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))

from agents.intelligence.schemas import SignOffContext  # noqa: E402
from config import settings  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(name)


async def main() -> int:
    if settings.intel_backend != "managed" or not settings.managed_l3_coordinator_agent_id:
        print("SKIP: INTEL_BACKEND != 'managed' or L3 agents not configured "
              "(run scripts.setup_l3_agents and set INTEL_BACKEND=managed).")
        return 0

    from agents.intelligence.managed_supervisor import ManagedIntelligenceSupervisor

    events = []

    async def cb(event_type, agent_name, data):
        events.append(event_type)

    sup = ManagedIntelligenceSupervisor(event_callback=cb)
    ctx = SignOffContext(vacated_rank="Chief Officer", port="Singapore", vessel="MV Pacific Star")

    print("Running managed L3 match (Chief Officer @ Singapore)...", flush=True)
    result = await sup.find_replacements(ctx, top_n=3)

    check("status is matched", result.status == "matched", result.status)
    check("3 investigator reports", len(result.reports) == 3,
          ", ".join(r.investigator for r in result.reports))
    check("top-3 ranked candidates", 1 <= len(result.candidates) <= 3,
          ", ".join(f"#{c.rank_position} {c.name} {c.score}" for c in result.candidates))
    check("every candidate carries rationale",
          all(c.rationale for c in result.candidates))
    check("ranked by score descending",
          all(result.candidates[i].score >= result.candidates[i + 1].score
              for i in range(len(result.candidates) - 1)))
    check("operators notified", len(result.notifications) >= 1,
          ", ".join(f"{n.role}/{n.channel}" for n in result.notifications))
    check("delegated to all 3 investigators (events)",
          events.count("intel_investigator_completed") == 3)
    check("fit graph built", bool(result.fit_graph), str(bool(result.fit_graph)))

    print(f"\n   timing: {result.timing}  ·  pool={result.pool_size} disq={result.disqualified}")
    print("RESULT:", "ALL CHECKS PASSED" if not FAILURES else f"FAILURES: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
