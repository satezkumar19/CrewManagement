# Wiring the Crew Matching skills into the existing agent

The existing `backend/agents/crew_matching_agent.py` defines `SYSTEM_ROLE`
as a Python string and passes it to `BaseAgent`. To switch to the new
skills-based prompt, change two things.

## 1. Replace the inline SYSTEM_ROLE

Before:

```python
SYSTEM_ROLE = """You are the Crew Matching Agent...
...
Always select the candidate with the highest overall score."""


class CrewMatchingAgent(BaseAgent):
    def __init__(self, event_callback=None):
        super().__init__(
            name="Crew Matching Agent",
            role=SYSTEM_ROLE,
            tools=TOOLS,
            event_callback=event_callback,
        )
```

After:

```python
from agents.skills import build_instructions


class CrewMatchingAgent(BaseAgent):
    def __init__(self, event_callback=None):
        super().__init__(
            name="Crew Matching Agent",
            role=build_instructions("crew_matching"),
            tools=TOOLS,
            event_callback=event_callback,
        )
```

You can delete the `SYSTEM_ROLE` constant — its content is now spread
across `skills/crew_matching/*.md`.

## 2. Update the managed-agent provisioning script

`scripts/setup_managed_agents.py` creates persisted Anthropic agents. The
system prompt sent to Anthropic should match what the local agent uses.

Wherever the script currently does something like:

```python
client.beta.agents.create(
    name="Crew Matching",
    system=CREW_MATCHING_SYSTEM,
    tools=CREW_MATCHING_TOOLS,
    environment_id=env_id,
)
```

Replace `CREW_MATCHING_SYSTEM` with:

```python
from agents.skills import build_instructions

client.beta.agents.create(
    name="Crew Matching",
    system=build_instructions("crew_matching"),
    tools=CREW_MATCHING_TOOLS,
    environment_id=env_id,
)
```

## 3. Re-provision the managed agent

Because the system prompt is persisted into Anthropic when the agent is
created, you must update the persisted agent. Two options:

- **Update in place** if your client exposes `client.beta.agents.update()`
  — pass the new `system` string. This avoids creating duplicates.
- **Re-create and rewrite IDs**: only safe if you also clear
  `backend/managed_agents.json`. Avoid this unless you understand the
  duplication risk called out in `CLAUDE.md`.

## 4. Smoke test

```
cd backend
python -m agents.skills.loader crew_matching
```

The dump should begin with the system prompt, followed by the scoring
rubric, certification matching, rank equivalence, port proximity, fallback
strategy, then the shared glossary and error conventions, each separated
by `---`.

Run the existing workflow against a seeded crew and confirm:

- `top_match` still returns a valid crew member.
- `match_reasons` now contains the stable tokens defined in
  `shared/error-conventions.md` when concerns exist.
- The legacy "Default selection" fallback (picking `crew_list[0]`) is no
  longer reached — instead the agent returns a `null` `top_match` with a
  `recommendation` field when fallback exhaustion occurs (see
  `fallback-strategy.md`).
