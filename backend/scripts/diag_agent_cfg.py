"""Read-only: print the LIVE travel agent's system prompt + attached skills, and
compare against the in-repo SYSTEM_ROLE. No inference tokens — control-plane reads."""
import json
import os
import anthropic
from config import settings
from agents.travel_agent import SYSTEM_ROLE

c = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key)

ids = json.load(open(settings.managed_agents_ids_file))
travel = ids["specialists"]["travel"]
aid = travel["agent_id"]
print("travel agent_id:", aid, "version:", travel.get("name"))

ag = c.beta.agents.retrieve(aid)
live_system = getattr(ag, "system", "") or ""
live_skills = getattr(ag, "skills", None)
live_tools = getattr(ag, "tools", None)

print("\n--- LIVE system prompt (first 400 chars) ---")
print(live_system[:400])
print("\n--- LIVE skills ---")
print(live_skills)
print("\n--- LIVE tools ---")
print([getattr(t, "type", None) or getattr(t, "name", None) for t in (live_tools or [])])

key_instruction = "FIRST open and read the relevant Skill"
print("\n=== VERDICT ===")
print("live prompt contains skill-read instruction:", key_instruction in live_system)
print("live prompt == repo SYSTEM_ROLE          :", live_system.strip() == SYSTEM_ROLE.strip())
print("live agent has skills attached           :", bool(live_skills))
