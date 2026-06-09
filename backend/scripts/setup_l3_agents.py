"""
One-time setup for the L3 Intelligence Managed-Agents topology.

Creates the 3 specialist investigator sub-agents (Crew Intel / Contract-Wage Intel /
Vessel Ops Intel) and the multiagent coordinator (the Intelligence Supervisor) on your
Anthropic organization, then caches their IDs to managed_l3_agents.json (loaded
automatically by config.py at startup).

Reuses the environment from managed_agents.json when present (so L3 shares the existing
maritime environment) — otherwise creates a fresh one.

Run ONCE (re-running creates duplicate agents):

    cd backend
    python -m scripts.setup_l3_agents

Then enable the managed backend:

    INTEL_BACKEND=managed uvicorn main:app --port 8000     # or set it in backend/.env

Prerequisites:
  * ANTHROPIC_API_KEY set, on an org with Managed Agents beta access
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.managed.client import ManagedAgentsClient  # noqa: E402
from config import settings  # noqa: E402


def _existing_environment_id() -> str:
    """Reuse the environment from managed_agents.json if it exists."""
    path = settings.managed_agents_ids_file
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f).get("environment_id", "") or ""
        except Exception:
            return ""
    return ""


async def main() -> None:
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    env_id = _existing_environment_id()
    print(
        "Creating L3 Intelligence Managed Agents (3 specialists + coordinator)"
        + (f", reusing environment {env_id}..." if env_id else ", creating a new environment...")
    )
    client = ManagedAgentsClient()
    ids = await client.setup_intelligence(environment_id=env_id or None)

    out_path = settings.managed_l3_agents_ids_file
    with open(out_path, "w") as f:
        json.dump(ids, f, indent=2)

    print("\nOK Setup complete. IDs cached to:", out_path)
    print(json.dumps(ids, indent=2))
    print("\nEnable the managed backend with INTEL_BACKEND=managed (env or backend/.env).")
    print("For container deploys, set instead:")
    print(f"  MANAGED_L3_ENVIRONMENT_ID={ids['environment_id']}")
    print(f"  MANAGED_L3_COORDINATOR_AGENT_ID={ids['coordinator_agent_id']}")


if __name__ == "__main__":
    asyncio.run(main())
