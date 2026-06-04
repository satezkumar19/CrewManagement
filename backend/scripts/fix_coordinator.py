"""
Repair the existing coordinator agent in place (new version) so it carries the
agent toolset + the multiagent roster — the config required for it to delegate.

Use this instead of re-running setup: it updates the SAME coordinator ID (so
managed_agents.json stays valid) and does not orphan the specialist agents.

    cd backend && python -m scripts.fix_coordinator
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to cp1252, which can't encode the ✅ status emoji and
# crashes the script AFTER the update already applied. Force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import anthropic  # noqa: E402
from agents.managed.registry import coordinator_agent_config  # noqa: E402
from config import settings  # noqa: E402


async def main() -> None:
    with open(settings.managed_agents_ids_file) as f:
        ids = json.load(f)

    coordinator_id = ids["coordinator_agent_id"]
    roster_ids = [s["agent_id"] for s in ids["specialists"].values()]
    if not roster_ids:
        print("ERROR: no specialist agent IDs in", settings.managed_agents_ids_file)
        sys.exit(1)

    cfg = coordinator_agent_config(roster_ids)
    print(f"Updating coordinator {coordinator_id} with tools={cfg['tools']} "
          f"and roster of {len(roster_ids)} agents...")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    # update() is an optimistic-lock write — it requires the current version.
    current = await client.beta.agents.retrieve(coordinator_id)
    current_version = getattr(current, "version", None)
    print("Current version:", current_version)
    updated = await client.beta.agents.update(
        coordinator_id, version=current_version, **cfg
    )

    print("✅ Coordinator updated. New version:", getattr(updated, "version", "?"))
    print("   New sessions (sign-off runs) will use this version automatically.")


if __name__ == "__main__":
    asyncio.run(main())
