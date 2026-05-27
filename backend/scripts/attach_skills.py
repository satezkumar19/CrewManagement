"""
Attach declared Agent Skills to EXISTING specialist agents (in place).

Attaches the skills a specialist declares in registry.SPECIALIST_SKILLS to its
already-provisioned agent. Uploads each skill once and patches the
already-provisioned agent via agents.update — so the agent_id stays stable and
the coordinator's roster is untouched. Uploaded skill_ids are written back into
managed_agents.json.

Usage (run after scripts.setup_managed_agents):

    cd backend
    python -m scripts.attach_skills travel          # one specialist
    python -m scripts.attach_skills travel notification
    python -m scripts.attach_skills                 # all specialists that declare skills

Notes:
  * agents.update REPLACES the agent's skill list, so this attaches exactly the
    skills currently declared for that specialist.
  * Idempotent on display_title: re-running adds a new VERSION to the existing
    skill (the API rejects duplicate titles) and re-points the agent at it.

Prerequisites:
  * managed_agents.json present (run scripts.setup_managed_agents first)
  * ANTHROPIC_API_KEY set, on an org with Managed Agents + Skills beta access
"""
import asyncio
import json
import os
import sys

# Allow `python scripts/attach_skills.py` from the backend dir too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.managed.client import ManagedAgentsClient  # noqa: E402
from agents.managed.registry import (  # noqa: E402
    SPECIALIST_SKILLS,
    specialist_config_with_skills,
    specialist_skill_specs,
)
from config import settings  # noqa: E402


async def attach_for_key(client: ManagedAgentsClient, ids: dict, key: str) -> bool:
    """Upload + attach the declared skills for one specialist key. Returns True on change."""
    specs = specialist_skill_specs(key)
    if not specs:
        print(f"  '{key}': no skills declared — skipping.")
        return False

    entry = ids.get("specialists", {}).get(key)
    if not entry or not entry.get("agent_id"):
        print(f"  '{key}': no agent_id in managed_agents.json — skipping.")
        return False

    skill_ids = []
    for spec in specs:
        print(f"  '{key}': uploading '{spec['display_title']}' ...")
        skill_id = await client.upload_skill(spec["dir"], spec["display_title"])
        skill_ids.append(skill_id)
        print(f"           -> {skill_id}")

    agent_id = entry["agent_id"]
    skills = [{"type": "custom", "skill_id": sid} for sid in skill_ids]
    # Re-assert the full specialist config (incl. the agent toolset skills need) and
    # pass the current version — agents.update is an optimistic-locked replace.
    cfg = specialist_config_with_skills(key, skills)
    current = await client.client.beta.agents.retrieve(agent_id)
    agent = await client.client.beta.agents.update(agent_id, version=current.version, **cfg)
    entry["skill_ids"] = skill_ids
    print(f"  '{key}': attached {len(skill_ids)} skill(s) to {agent_id} (v{agent.version})")
    return True


async def main() -> None:
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    ids_path = settings.managed_agents_ids_file
    if not os.path.exists(ids_path):
        print(f"ERROR: {ids_path} not found. Run scripts.setup_managed_agents first.")
        sys.exit(1)

    with open(ids_path) as f:
        ids = json.load(f)

    requested = sys.argv[1:]
    keys = requested or [k for k in SPECIALIST_SKILLS if specialist_skill_specs(k)]
    unknown = [k for k in requested if k not in SPECIALIST_SKILLS]
    if unknown:
        print(f"ERROR: no skills declared for: {', '.join(unknown)}")
        print(f"Available: {', '.join(k for k in SPECIALIST_SKILLS)}")
        sys.exit(1)

    print(f"Attaching skills for: {', '.join(keys)}")
    client = ManagedAgentsClient()

    changed = False
    for key in keys:
        changed |= await attach_for_key(client, ids, key)

    if changed:
        with open(ids_path, "w") as f:
            json.dump(ids, f, indent=2)
        print("\n[OK] Done. Updated", ids_path)
    else:
        print("\nNothing to attach.")


if __name__ == "__main__":
    asyncio.run(main())
