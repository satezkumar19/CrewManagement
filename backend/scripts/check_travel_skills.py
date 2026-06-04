"""
Verify the LIVE Travel Agent matches THIS checkout's canonical skill config — i.e.
exactly the skills declared in registry.SPECIALIST_SKILLS['travel'] (the four travel
policy skills) and a prompt that tells it to use them, with the coordinator pinned to
that version. Use it to detect drift, e.g. an external script/notebook re-attaching a
different skill (the "Port to Airport Mapping" skill that kept overwriting ours).

    cd backend
    python -m scripts.check_travel_skills      # prints PASS / FAIL (exit 1 on drift)

On FAIL, restore the canonical config with:
    python -m scripts.attach_skills travel
    python -m scripts.fix_coordinator

Prerequisites: ANTHROPIC_API_KEY set; managed_agents.json present.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to cp1252 and mangle the PASS/FAIL status emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import anthropic  # noqa: E402
from agents.managed.registry import SPECIALIST_SKILLS  # noqa: E402
from config import settings  # noqa: E402

EXPECTED_FOLDERS = [folder for folder, _title in SPECIALIST_SKILLS.get("travel", [])]


async def main() -> None:
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        sys.exit(2)

    ids = json.load(open(settings.managed_agents_ids_file))
    travel = ids["specialists"]["travel"]
    tid = travel["agent_id"]
    expected_ids = set(travel.get("skill_ids") or [])

    c = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    agent = (await c.beta.agents.retrieve(tid)).model_dump(mode="json")
    live_ids = {s.get("skill_id") for s in (agent.get("skills") or [])}
    prompt = agent.get("system") or ""

    coord = (await c.beta.agents.retrieve(ids["coordinator_agent_id"])).model_dump(mode="json")
    roster = {a["id"]: a.get("version") for a in coord["multiagent"]["agents"]}

    problems = []
    if not expected_ids:
        problems.append("managed_agents.json has no travel skill_ids — run `attach_skills travel`.")
    elif live_ids != expected_ids:
        problems.append(
            "attached skills differ from this checkout:\n"
            f"     live     = {sorted(live_ids)}\n"
            f"     expected = {sorted(expected_ids)}"
        )
    missing = [f for f in EXPECTED_FOLDERS if f not in prompt]
    if missing:
        problems.append(f"prompt does not reference the expected skill(s): {missing}")
    if roster.get(tid) != agent.get("version"):
        problems.append(
            f"coordinator pins travel at v{roster.get(tid)} but live travel is "
            f"v{agent.get('version')} — run `fix_coordinator`."
        )

    print(f"Travel Agent: live v{agent.get('version')} | coordinator pins v{roster.get(tid)}")
    print(f"Expected {len(EXPECTED_FOLDERS)} skills: {EXPECTED_FOLDERS}")
    print(f"Live skill_ids ({len(live_ids)}): {sorted(live_ids)}")

    if problems:
        print("\nFAIL — drift detected:")
        for p in problems:
            print("  - " + p)
        print("\nRestore this checkout's canonical config with:")
        print("  python -m scripts.attach_skills travel")
        print("  python -m scripts.fix_coordinator")
        sys.exit(1)

    print("\nPASS — live Travel Agent matches this checkout's canonical skills.")


if __name__ == "__main__":
    asyncio.run(main())
