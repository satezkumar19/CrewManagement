# Agent Skills

This directory holds the prompt-time knowledge for every specialist agent.
A "skill" is a markdown file that captures one focused piece of guidance —
a scoring rubric, a domain glossary, an error-handling convention. Skills
are loaded into the agent's system prompt at provisioning time so they
shape reasoning without consuming runtime tool calls.

## Layout

```
skills/
├── README.md                ← this file
├── __init__.py              ← exposes build_instructions(), list_agents()
├── loader.py                ← assembles system_prompt + skills + shared
├── shared/                  ← skills loaded by every agent
│   ├── maritime-glossary.md
│   └── error-conventions.md
└── crew_matching/           ← one folder per specialist
    ├── system_prompt.md
    ├── scoring-rubric.md
    ├── certification-matching.md
    ├── rank-equivalence.md
    ├── port-proximity.md
    └── fallback-strategy.md
```

To add another specialist (e.g., compliance):

```
skills/compliance/
├── system_prompt.md
├── mlc-2006-checks.md
├── flag-state-rules.md
└── documentation-audit.md
```

Then call `build_instructions("compliance")` from `compliance_agent.py`.

## How agents consume skills

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

When provisioning a managed agent in
`scripts/setup_managed_agents.py`, pass the same string as the `system`
parameter so the persisted Anthropic agent carries the full instructions.

## Authoring conventions

- One concept per file. Keep skills small and focused — easier to review,
  easier to A/B test, easier to retire.
- Use the file name as the skill title. Prefer kebab-case
  (`scoring-rubric.md` not `Scoring Rubric.md`).
- Open every skill file with `# Skill: <Name>` as the first line. The
  loader does not require this but it helps human readers.
- Reference other skills by file name in prose. Don't import or include
  them — the loader concatenates everything into one prompt anyway.
- Keep tokens stable. Anything UPPER_SNAKE_CASE that appears in a skill
  is part of the agent contract; downstream code may parse it.

## Versioning

Treat skill files like code. Review them in PRs. When a regulation or
business rule changes, edit the relevant skill and ship the change as a
normal commit. Avoid burying business rules inside Python string literals.

## Why not load everything?

Each specialist sees only its own skill folder plus `shared/`. This keeps
the system prompt focused and saves context budget. The Travel agent does
not need MLC compliance rules; the Compliance agent does not need port
proximity scoring.

## Verifying

Run the loader as a script to dump the assembled instructions for any
agent:

```
cd backend
python -m agents.skills.loader crew_matching
```
