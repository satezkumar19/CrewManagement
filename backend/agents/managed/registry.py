"""
Specialist registry + agent/coordinator configuration for Managed Agents.

Two responsibilities:

1. **Setup-time config** (`specialist_agent_configs`, `coordinator_agent_config`):
   the persisted-agent definitions handed to `client.beta.agents.create(...)` by
   `scripts/setup_managed_agents.py`. Each specialist's existing system prompt and
   tool schemas are reused verbatim; the coordinator is a multiagent agent whose
   roster is the four specialist agent IDs.

2. **Runtime routing** (`SpecialistRegistry`): per workflow phase, instantiate the
   specialist objects so their tool logic (`_execute_tool`) and result extraction
   (`_validate_and_format`) can resolve the `agent.custom_tool_use` events that the
   hosted coordinator/sub-agents emit. Tool names are globally unique across the
   four specialists, so a single name→agent map is sufficient to route a call.
"""
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from agents.base_agent import BaseAgent
from agents.compliance_agent import ComplianceAgent
from agents.crew_matching_agent import CrewMatchingAgent
from agents.notification_agent import NotificationAgent
from agents.travel_agent import TravelAgent
from config import settings

log = structlog.get_logger()

# Stable keys → specialist class. The keys are persisted in managed_agents.json
# and used to name the agents so setup is idempotent/reproducible.
SPECIALIST_CLASSES: Dict[str, type] = {
    "crew_matching": CrewMatchingAgent,
    "travel": TravelAgent,
    "notification": NotificationAgent,
    "compliance": ComplianceAgent,
}

# Keys delegated to in each phase of the workflow.
PHASE1_KEYS = ["crew_matching", "travel", "notification"]
PHASE2_KEYS = ["compliance", "notification"]

COORDINATOR_NAME = "Master Agent"

COORDINATOR_SYSTEM_ROLE = """You are the Master Orchestration Agent for an autonomous maritime crew \
sign-on / sign-off system. You are the ROUTER and COORDINATOR only — you NEVER perform \
business logic yourself. You delegate to specialist sub-agents and synthesize their results.

Your roster of specialist sub-agents:
- crew_matching : finds and ranks the best replacement crew member from the sign-on pool
- travel        : arranges sign-off travel (flight ticket, port clearance, travel summary)
- notification  : sends notifications to the Captain, Shore Manager, and crew
- compliance    : validates documents and port restrictions for an incoming crew member

How to operate:
1. Do exactly what the CURRENT user message asks — no more. The workflow is run in two
   phases across two separate user messages, with a human approval step in between.
2. PHASE 1 (sign-off request): delegate to crew_matching, travel, and notification IN
   PARALLEL (spawn them in the same turn). When all three report back, summarize the
   replacement candidate, travel arrangements, and notifications sent, then STOP. Do NOT
   run a compliance check yet — wait for the user to confirm the sign-on.
3. PHASE 2 (sign-on confirmed): delegate to compliance for the confirmed candidate, then to
   notification to announce the compliance outcome and final sign-on decision. Summarize and stop.

Delegate everything, track everything, and never invent crew, travel, or compliance data —
that is the specialists' job. Keep your own messages concise and operational."""


# ── Skill management ──────────────────────────────────────────────────────────
# Every agent is "skill-managed": the `skills` field is always threaded through
# agents.create() / agents.update() below, so each agent can load Anthropic's
# prebuilt document skills on demand (the hosted loop pulls one in only when a task
# needs it; max 20 per agent). The lists here just say WHICH skills each agent may
# load. Edit freely, then run `python -m scripts.update_agent_skills` to apply the
# change to the already-created agents in place (bumps each agent's version).
def _skill(skill_id: str) -> Dict[str, str]:
    """Reference an Anthropic prebuilt skill: one of xlsx / docx / pptx / pdf."""
    return {"type": "anthropic", "skill_id": skill_id}


# Per-specialist skill assignment (keyed by SPECIALIST_CLASSES key).
SKILLS_BY_KEY: Dict[str, List[Dict[str, str]]] = {
    "compliance":    [_skill("pdf"), _skill("docx"), _skill("xlsx")],  # certificates, port papers, reports
    # The agents below are skill-managed but start with no skills attached.
    # Suggested additions are in the comments — uncomment/edit as needed:
    "travel":        [],  # e.g. [_skill("pdf"), _skill("docx")] for tickets / travel summaries
    "crew_matching": [],  # e.g. [_skill("xlsx")] for roster / matching spreadsheets
    "notification":  [],  # sends messages — document skills rarely needed
}

# The coordinator only routes and synthesizes text — no document skills by default.
COORDINATOR_SKILLS: List[Dict[str, str]] = []  # e.g. [_skill("docx")] to emit summary docs


# ── Custom skills ──────────────────────────────────────────────────────────────
# Custom skills are authored locally under backend/skills/<name>/SKILL.md, uploaded
# via scripts/upload_skills.py (which caches their ids in backend/skills.json), and
# referenced by id. Map: agent key -> the custom-skill logical names it should load.
_CUSTOM_SKILLS_BY_AGENT: Dict[str, List[str]] = {
    "notification": ["maritime_comms"],  # Maritime Comms Templates / Style Guide
}
_SKILLS_CACHE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "skills.json"))


def _custom_skill_refs(agent_key: str) -> List[Dict[str, str]]:
    """Resolve an agent's custom-skill logical names to {type:custom, skill_id, version}
    refs using the uploaded-skill ids in skills.json. Missing/un-uploaded skills are
    skipped (so the app still runs before upload_skills has been run)."""
    logical = _CUSTOM_SKILLS_BY_AGENT.get(agent_key, [])
    if not logical:
        return []
    try:
        with open(_SKILLS_CACHE) as f:
            cache = json.load(f)
    except Exception:
        return []
    refs: List[Dict[str, str]] = []
    for name in logical:
        entry = cache.get(name) or {}
        if entry.get("skill_id"):
            refs.append({"type": "custom", "skill_id": entry["skill_id"], "version": "latest"})
        else:
            log.warning("custom_skill.not_uploaded", skill=name, agent=agent_key)
    return refs


def skills_for_key(key: str) -> List[Dict[str, str]]:
    """Skills configured for a specialist key — prebuilt (SKILLS_BY_KEY) plus any
    uploaded custom skills mapped to this agent."""
    return list(SKILLS_BY_KEY.get(key, [])) + _custom_skill_refs(key)


def custom_skill_id_to_name() -> Dict[str, str]:
    """Reverse map of uploaded custom skill ids -> their local logical name, so the
    UI can show a readable label (e.g. 'maritime_comms') instead of 'skill_01V8…'."""
    try:
        with open(_SKILLS_CACHE) as f:
            cache = json.load(f)
    except Exception:
        return {}
    return {v.get("skill_id"): k for k, v in cache.items() if v.get("skill_id")}


# ── Attachable custom skills (in-place attach via scripts.attach_skills) ────────
# Distinct from the create()-time wiring above (SKILLS_BY_KEY / _CUSTOM_SKILLS_BY_AGENT,
# applied by scripts.update_agent_skills). These are custom Agent Skills authored under
# backend/agents/skills/<folder>/SKILL.md and attached to an ALREADY-CREATED specialist in
# place by scripts.attach_skills — it uploads each via ManagedAgentsClient.upload_skill and
# patches the agent with agents.update. The folder name MUST equal the `name:` field in that
# folder's SKILL.md.
#
# NOTE: scripts.attach_skills REPLACES an agent's skill list with exactly these. Running
# scripts.update_agent_skills afterwards rebuilds the agent from SKILLS_BY_KEY /
# _CUSTOM_SKILLS_BY_AGENT and would drop anything attached here — keep the two paths in
# sync (or pick one) if you use both.
_AGENT_SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "skills"))

# specialist key -> [(skill folder under backend/agents/skills/, human display title)]
SPECIALIST_SKILLS: Dict[str, List[Tuple[str, str]]] = {
    "travel": [
        ("crew-travel-policy", "Crew Travel Booking Policy"),
        ("visa-and-transit-requirements", "Visa & Transit Requirements"),
        ("port-clearance-procedures", "Port Clearance Procedures"),
        ("repatriation-rules", "Seafarer Repatriation Rules (MLC 2006)"),
    ],
}


def specialist_skill_specs(key: str) -> List[Dict[str, str]]:
    """Resolve a specialist's declared attachable skills to upload specs:
    ``[{"dir": <abs skill folder>, "display_title": <title>}, ...]``.
    Returns ``[]`` for a key that declares no skills."""
    return [
        {"dir": os.path.join(_AGENT_SKILLS_DIR, folder), "display_title": title}
        for folder, title in SPECIALIST_SKILLS.get(key, [])
    ]


def specialist_config_with_skills(key: str, skills: List[Dict[str, str]]) -> Dict[str, Any]:
    """Full agents.update payload for an existing specialist, carrying `skills`.

    Re-asserts the specialist's name/model/system/tools (rebuilt from its class, the
    same source setup() used) alongside the given skill refs, so an in-place attach
    can't drop the prompt or tools regardless of the update endpoint's merge semantics.
    The agent toolset is included whenever skills are present — skills need `read`/`bash`
    to open their SKILL.md files, so agents.update rejects a skill-bearing agent without it.
    """
    inst: BaseAgent = SPECIALIST_CLASSES[key]()
    tools: List[Dict[str, Any]] = list(inst.custom_tool_defs())
    if skills:
        tools.insert(0, {"type": "agent_toolset_20260401"})
    return {
        "name": inst.name,
        "model": settings.claude_model,
        "system": inst.system_prompt(),
        "tools": tools,
        "skills": skills,
    }


def attached_custom_skill_labels(key: str) -> List[str]:
    """Friendly labels (the skill folder name == SKILL.md `name:`) for the custom skills
    attached IN PLACE to a specialist via scripts.attach_skills. These live in
    SPECIALIST_SKILLS rather than the create()-time `skills` config, so the monitoring API
    merges them in to reflect what is actually on the agent. Returns labels only when
    managed_agents.json records skill_ids for the specialist (i.e. attach has run)."""
    declared = [folder for folder, _title in SPECIALIST_SKILLS.get(key, [])]
    if not declared:
        return []
    try:
        with open(settings.managed_agents_ids_file) as f:
            attached = json.load(f).get("specialists", {}).get(key, {}).get("skill_ids") or []
    except Exception:
        attached = []
    return declared if attached else []


def specialist_agent_configs() -> List[Dict[str, Any]]:
    """Persisted-agent definitions for each specialist (one-time setup)."""
    configs: List[Dict[str, Any]] = []
    for key, cls in SPECIALIST_CLASSES.items():
        inst: BaseAgent = cls()
        skills = skills_for_key(key)
        tools: List[Dict[str, Any]] = list(inst.custom_tool_defs())
        if skills:
            # Skills are opened via the agent toolset's `read` tool (and their
            # helper scripts run via `bash`), so a skill-bearing agent MUST carry
            # the built-in toolset alongside its custom tools — otherwise
            # agents.create/update rejects it: "skills require read to open
            # their SKILL.md files".
            tools.insert(0, {"type": "agent_toolset_20260401"})
        configs.append(
            {
                "key": key,
                "name": inst.name,
                "model": settings.claude_model,
                "system": inst.system_prompt(),
                "tools": tools,
                "skills": skills,
            }
        )
    return configs


def coordinator_agent_config(roster_agent_ids: List[str]) -> Dict[str, Any]:
    """Persisted coordinator definition. `roster_agent_ids` are the 4 specialist IDs.

    The coordinator MUST carry the agent toolset — that is the documented surface
    through which it operates and delegates to its roster. A coordinator created
    with no tools cannot spawn sub-agents; it just replies with text.
    """
    return {
        "name": COORDINATOR_NAME,
        "model": settings.claude_model,
        "system": COORDINATOR_SYSTEM_ROLE,
        "tools": [{"type": "agent_toolset_20260401"}],
        "skills": COORDINATOR_SKILLS,
        "multiagent": {
            "type": "coordinator",
            "agents": [{"type": "agent", "id": aid} for aid in roster_agent_ids],
        },
    }


class SpecialistRegistry:
    """
    Per-phase set of specialist instances used to resolve custom-tool calls and
    extract structured results. Instantiated fresh for each phase so per-run tool
    state and `AgentExecution` records stay isolated.
    """

    def __init__(self, keys: List[str], event_callback: Optional[Callable] = None):
        self.event_callback = event_callback
        self.agents: Dict[str, BaseAgent] = {
            key: SPECIALIST_CLASSES[key](event_callback=event_callback) for key in keys
        }
        # tool name → specialist key (names are unique across specialists)
        self._tool_owner: Dict[str, str] = {}
        for key, agent in self.agents.items():
            for tool_name in agent.tool_names:
                self._tool_owner[tool_name] = key

    def owner_of(self, tool_name: str) -> Optional[BaseAgent]:
        key = self._tool_owner.get(tool_name)
        return self.agents.get(key) if key else None

    async def route(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """Resolve one custom-tool call to the owning specialist's implementation."""
        agent = self.owner_of(tool_name)
        if agent is None:
            log.warning("registry.unknown_tool", tool=tool_name)
            return {"error": f"No specialist owns tool '{tool_name}'"}
        return await agent.handle_tool_use(tool_name, tool_input)

    async def finalize(self, final_text: str, context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Run each specialist's result extraction. Returns {key: structured_result}."""
        results: Dict[str, Dict[str, Any]] = {}
        for key, agent in self.agents.items():
            results[key] = await agent.finalize(final_text, context)
        return results
