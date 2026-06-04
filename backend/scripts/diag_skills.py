"""Throwaway diagnostic: inspect the most recent managed session's threads and
print every tool_use, flagging anything that looks like a SKILL.md / skills access.
Tells us whether the Travel thread actually opened its skills this run."""
import json
import os
import anthropic
from config import settings
from agents.managed.client import _looks_like_skill_access, _skill_name_from_payload, _coerce_input

# Prefer the real env-var key the running app uses; the .env value may be a stale
# placeholder. Run this from the same shell that launches the backend.
c = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key)

sessions = list(c.beta.sessions.list(betas=["skills-2025-10-02"]))
print(f"sessions returned: {len(sessions)}")
for s in sessions[:3]:
    print("  session", getattr(s, "id", None), getattr(s, "created_at", None))

if not sessions:
    raise SystemExit("no sessions")

sid = sessions[0].id
print(f"\n=== inspecting most recent session {sid} ===")
threads = list(c.beta.sessions.threads.list(sid))
print(f"threads: {len(threads)}")
for t in threads:
    tid = getattr(t, "id", None)
    agent = getattr(t, "agent", None)
    tname = getattr(agent, "name", None) if agent else None
    print(f"\n--- thread {tid}  agent={tname!r} ---")
    evs = list(c.beta.sessions.threads.events.list(tid, session_id=sid))
    tool_uses = [e for e in evs if getattr(e, "type", "") == "agent.tool_use"]
    print(f"   total events={len(evs)}  tool_use events={len(tool_uses)}")
    for e in tool_uses:
        name = getattr(e, "name", None)
        inp = _coerce_input(getattr(e, "input", None))
        payload = {"name": name, "input": inp}
        is_skill = _looks_like_skill_access(payload)
        skill = _skill_name_from_payload(payload) if is_skill else None
        blob = json.dumps(inp, default=str)[:120]
        flag = f"  <== SKILL ACCESS skill={skill!r}" if is_skill else ""
        print(f"     tool={name!r:8} input={blob}{flag}")
