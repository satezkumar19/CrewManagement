"""
ManagedAgentsClient — thin wrapper over the Claude Managed Agents API.

Two surfaces:

* **Control plane (setup, run once):** `setup()` creates the persisted environment,
  the four specialist agents, and the multiagent coordinator, returning their IDs.
  Called by `scripts/setup_managed_agents.py`.

* **Data plane (per workflow turn):** `create_session()` opens a coordinator session;
  `run_turn()` streams one turn of that session — relaying events to a callback,
  resolving `agent.custom_tool_use` events against a `SpecialistRegistry`, and
  accumulating token usage. The same session is reused across the sign-off (Phase 1)
  and sign-on/compliance (Phase 2) turns so the coordinator keeps its context.

Targets the documented SDK surface (`anthropic>=0.92.0`, beta `managed-agents-2026-04-01`,
set automatically by the SDK on `client.beta.*` calls). If your installed SDK differs,
the method/field names here are the ones to verify first.
"""
import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import anthropic
import structlog

from agents.managed.registry import (
    coordinator_agent_config,
    specialist_agent_configs,
)
from config import settings

log = structlog.get_logger()

# on_event(event_type: str, payload: dict) -> Awaitable — used to relay the raw
# session event stream to the caller (which maps it onto the WebSocket vocabulary).
EventCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]


def _block_text(block: Any) -> str:
    if getattr(block, "type", None) == "text":
        return getattr(block, "text", "") or ""
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_block_text(b) for b in content)
    return ""


def _to_result_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, default=str)
    except Exception:
        return str(output)


# Skills API beta header for skills.create (matches scripts.upload_skills).
_SKILLS_BETA = "skills-2025-10-02"
_SKILL_CONTENT_TYPES = {".md": "text/markdown", ".txt": "text/plain", ".json": "application/json"}


def _skill_files(skill_dir: str) -> List[Tuple[str, bytes, str]]:
    """Read every file under a skill folder as (relpath, bytes, content_type) tuples,
    naming each relative to the folder's PARENT so paths read '<skill-folder>/SKILL.md' —
    the Skills API requires SKILL.md to sit in exactly one top-level folder."""
    parent = os.path.dirname(os.path.normpath(skill_dir))
    files: List[Tuple[str, bytes, str]] = []
    for root, _dirs, names in os.walk(skill_dir):
        for n in names:
            path = os.path.join(root, n)
            # Skills API path keys must use forward slashes — normalize Windows '\'.
            rel = os.path.relpath(path, parent).replace(os.sep, "/")
            ct = _SKILL_CONTENT_TYPES.get(os.path.splitext(n)[1], "application/octet-stream")
            with open(path, "rb") as f:
                files.append((rel, f.read(), ct))
    return files


# Built-in toolset commands that, when they touch a path/command mentioning a
# skill, indicate the hosted agent is loading one of its configured Skills. There
# is no first-class "skill used" event in the managed stream, so this is the
# best-effort signal for the console's Skills lane.
_SKILL_FILE_TOOLS = ("read", "bash", "glob", "grep", "view")


def _looks_like_skill_access(payload: Dict[str, Any]) -> bool:
    name = (payload.get("name") or "").lower()
    if name not in _SKILL_FILE_TOOLS:
        return False
    try:
        blob = json.dumps(payload.get("input") or {}, default=str).lower()
    except Exception:
        return False
    return "skill" in blob


# Pull the skill's folder name out of a skill-file access so the console can show
# WHICH skill was used. Prefer the SKILL.md folder; fall back to the segment after
# a /skills/ path component.
_SKILL_MD_RE = re.compile(r"([A-Za-z0-9][\w.-]*)/SKILL\.md", re.IGNORECASE)
_SKILLS_DIR_RE = re.compile(r"(?:^|/)skills?/([A-Za-z0-9][\w.-]*)", re.IGNORECASE)


def _skill_name_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    try:
        blob = json.dumps(payload.get("input") or {}, default=str)
    except Exception:
        return None
    m = _SKILL_MD_RE.search(blob) or _SKILLS_DIR_RE.search(blob)
    if not m:
        return None
    # Normalize: the failed-setup workaround references "<name>.zip" under /mnt/skills.
    return m.group(1).removesuffix(".zip")


def _event_category(etype: str, payload: Dict[str, Any]) -> str:
    """Tag an event for the UI console lanes: context | loop | skill.

    Survives the event-type rename in master_agent (it lives in the payload, not
    the type), so the frontend can group by `data.category` directly.
    """
    if etype == "agent.thread_context_compacted":
        return "context"
    if etype == "agent.tool_use" and _looks_like_skill_access(payload):
        return "skill"
    # Everything else in the agent loop — model requests, tool calls, thread
    # lifecycle, messages, thinking. model-usage events also carry cache fields
    # the frontend additionally surfaces in the Context lane.
    return "loop"


def _summarize_event(event: Any) -> Dict[str, Any]:
    """Build a JSON-serializable payload from a session event for relay/inspection."""
    etype = getattr(event, "type", "")
    payload: Dict[str, Any] = {"event_id": getattr(event, "id", None)}

    agent_name = getattr(event, "agent_name", None)
    if agent_name:
        payload["agent_name"] = agent_name
    thread_id = getattr(event, "session_thread_id", None)
    if thread_id:
        payload["session_thread_id"] = thread_id

    if etype in ("agent.message", "agent.thinking"):
        payload["text"] = _content_text(getattr(event, "content", None))
    elif etype in ("agent.custom_tool_use", "agent.tool_use", "agent.mcp_tool_use"):
        payload["name"] = getattr(event, "name", None)
        payload["input"] = _coerce_input(getattr(event, "input", None))
    elif etype in ("agent.tool_result", "agent.mcp_tool_result"):
        payload["name"] = getattr(event, "name", None)
        payload["is_error"] = bool(getattr(event, "is_error", False))
    elif etype == "agent.thread_context_compacted":
        # Context management: history was summarized to fit the window.
        pre = getattr(event, "pre_compaction_tokens", None)
        if pre is not None:
            payload["pre_compaction_tokens"] = pre
    elif etype == "span.model_request_end":
        mu = getattr(event, "model_usage", None)
        if mu is not None:
            payload["model_usage"] = {
                "input_tokens": getattr(mu, "input_tokens", 0) or 0,
                "output_tokens": getattr(mu, "output_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(mu, "cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(mu, "cache_creation_input_tokens", 0) or 0,
            }
    elif etype in ("session.status_idle", "session.thread_status_idle"):
        sr = getattr(event, "stop_reason", None)
        payload["stop_reason"] = getattr(sr, "type", None) if sr else None

    payload["category"] = _event_category(etype, payload)
    if payload["category"] == "skill":
        skill = _skill_name_from_payload(payload)
        if skill:
            payload["skill"] = skill
    return payload


def _coerce_input(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    try:
        return dict(value)
    except Exception:
        return {}


class ManagedAgentsClient:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        # Skill loads are built-in read/bash calls that appear ONLY on sub-agent
        # thread streams, not the session stream we subscribe to. After each turn we
        # sweep the threads for them; this dedups across phases (same session).
        self._seen_skill_events: set = set()

    # ── Control plane (one-time setup) ─────────────────────────────────────────

    async def setup(self) -> Dict[str, Any]:
        """
        Create the environment, the four specialist agents, and the coordinator.
        Returns an ids dict suitable for persisting to managed_agents.json.
        Idempotency is the caller's concern — running this twice creates duplicates.
        """
        env = await self.client.beta.environments.create(
            name="maritime-crew-env",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        log.info("setup.environment", environment_id=env.id)

        specialists: Dict[str, Dict[str, str]] = {}
        roster_ids: List[str] = []
        for cfg in specialist_agent_configs():
            key = cfg.pop("key")
            agent = await self.client.beta.agents.create(**cfg)
            specialists[key] = {"agent_id": agent.id, "name": cfg["name"]}
            roster_ids.append(agent.id)
            log.info("setup.specialist", key=key, agent_id=agent.id)

        coordinator = await self.client.beta.agents.create(
            **coordinator_agent_config(roster_ids)
        )
        log.info("setup.coordinator", agent_id=coordinator.id)

        return {
            "environment_id": env.id,
            "coordinator_agent_id": coordinator.id,
            "specialists": specialists,
            "model": settings.claude_model,
        }

    async def setup_intelligence(self, environment_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Create the L3 Intelligence topology: the 3 specialist sub-agents (Crew /
        Contract-Wage / Vessel Ops) and the multiagent coordinator (the Supervisor),
        reusing `environment_id` when given (e.g. the one from managed_agents.json) else
        creating a fresh one. Returns an ids dict for managed_l3_agents.json.

        Idempotency is the caller's concern — re-running creates duplicate agents.
        """
        # Lazy import: keeps the L3 intelligence stack out of client.py's load path
        # (this client is also used by the unrelated sign-off workflow).
        from agents.intelligence.managed_registry import (
            intelligence_coordinator_config,
            intelligence_specialist_configs,
        )

        if environment_id:
            env_id = environment_id
            log.info("setup_l3.environment.reused", environment_id=env_id)
        else:
            env = await self.client.beta.environments.create(
                name="maritime-l3-intel-env",
                config={"type": "cloud", "networking": {"type": "unrestricted"}},
            )
            env_id = env.id
            log.info("setup_l3.environment.created", environment_id=env_id)

        specialists: Dict[str, Dict[str, str]] = {}
        roster_ids: List[str] = []
        for cfg in intelligence_specialist_configs():
            key = cfg.pop("key")
            agent = await self.client.beta.agents.create(**cfg)
            specialists[key] = {"agent_id": agent.id, "name": cfg["name"]}
            roster_ids.append(agent.id)
            log.info("setup_l3.specialist", key=key, agent_id=agent.id)

        coordinator = await self.client.beta.agents.create(
            **intelligence_coordinator_config(roster_ids)
        )
        log.info("setup_l3.coordinator", agent_id=coordinator.id)

        return {
            "environment_id": env_id,
            "coordinator_agent_id": coordinator.id,
            "specialists": specialists,
            "model": settings.claude_model,
        }

    async def _find_skill_id_by_title(self, display_title: str) -> Optional[str]:
        """Return the skill_id of an existing custom skill with this display_title, or
        None. Display titles are unique per org, so a match means we must version it
        rather than create a duplicate."""
        async for sk in self.client.beta.skills.list(betas=[_SKILLS_BETA]):
            if getattr(sk, "display_title", None) == display_title:
                return getattr(sk, "id", None)
        return None

    async def upload_skill(self, skill_dir: str, display_title: str) -> str:
        """Upload a custom Agent Skill from a local folder (must contain SKILL.md) and
        return its skill_id. Idempotent on display_title: if a skill with this title
        already exists it adds a new VERSION (the API rejects duplicate titles);
        otherwise it creates the skill."""
        files = _skill_files(skill_dir)
        if not any(rel.endswith("SKILL.md") for rel, _b, _ct in files):
            raise FileNotFoundError(f"No SKILL.md found under {skill_dir}")
        existing = await self._find_skill_id_by_title(display_title)
        if existing:
            await self.client.beta.skills.versions.create(
                existing, files=files, betas=[_SKILLS_BETA]
            )
            return existing
        resp = await self.client.beta.skills.create(
            display_title=display_title, files=files, betas=[_SKILLS_BETA]
        )
        return resp.id

    async def update_skills(self) -> Dict[str, Any]:
        """
        Apply the skills configured in registry.py to the EXISTING agents named in
        managed_agents.json, in place. Each agent keeps its ID; agents.update() bumps
        it to a new immutable version carrying the skills.

        We re-assert each agent's full config (rebuilt from the registry — the same
        source setup() used) alongside the skills, so prompts and tools can't be
        dropped regardless of the update endpoint's merge semantics. agents.update()
        requires the current version (optimistic lock), so we retrieve it first.
        Safe to re-run.
        """
        path = settings.managed_agents_ids_file
        with open(path) as f:
            ids = json.load(f)

        results: Dict[str, Any] = {}

        # Specialists: rebuild each full config (now includes skills) and update in place.
        specialist_cfgs = {c["key"]: c for c in specialist_agent_configs()}
        for key, info in ids["specialists"].items():
            cfg = dict(specialist_cfgs[key])
            cfg.pop("key")
            current = await self.client.beta.agents.retrieve(info["agent_id"])
            agent = await self.client.beta.agents.update(
                info["agent_id"], version=current.version, **cfg
            )
            results[key] = {
                "agent_id": agent.id,
                "version": agent.version,
                "skills": [s.get("skill_id") for s in cfg.get("skills", [])],
            }
            log.info("update_skills.specialist", key=key, agent_id=agent.id, version=agent.version)

        # Coordinator: rebuild full config with the same roster, now with skills.
        roster_ids = [info["agent_id"] for info in ids["specialists"].values()]
        coord_cfg = coordinator_agent_config(roster_ids)
        current = await self.client.beta.agents.retrieve(ids["coordinator_agent_id"])
        coord = await self.client.beta.agents.update(
            ids["coordinator_agent_id"], version=current.version, **coord_cfg
        )
        results["coordinator"] = {
            "agent_id": coord.id,
            "version": coord.version,
            "skills": [s.get("skill_id") for s in coord_cfg.get("skills", [])],
        }
        log.info("update_skills.coordinator", agent_id=coord.id, version=coord.version)

        return results

    # ── Data plane (per workflow) ──────────────────────────────────────────────

    async def create_session(self, title: str) -> str:
        """Open a coordinator session in the configured environment."""
        if not settings.managed_coordinator_agent_id or not settings.managed_environment_id:
            raise RuntimeError(
                "Managed Agents not configured. Run scripts/setup_managed_agents.py "
                "(or set MANAGED_COORDINATOR_AGENT_ID / MANAGED_ENVIRONMENT_ID)."
            )
        session = await self.client.beta.sessions.create(
            agent=settings.managed_coordinator_agent_id,
            environment_id=settings.managed_environment_id,
            title=title,
        )
        return session.id

    async def run_turn(
        self,
        session_id: str,
        message: str,
        registry: "Any",  # SpecialistRegistry — typed loosely to avoid import cycle
        on_event: Optional[EventCallback] = None,
    ) -> Dict[str, Any]:
        """
        Drive ONE turn of the coordinator session: open the stream, send `message`,
        resolve custom-tool calls via `registry`, relay events via `on_event`, and
        return {"text", "usage"} when the session goes idle/terminated.
        """
        timeout = settings.session_turn_timeout_seconds
        try:
            return await asyncio.wait_for(
                self._run_turn(session_id, message, registry, on_event),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.error("session.turn.timeout", session_id=session_id, timeout=timeout)
            raise

    async def _run_turn(
        self,
        session_id: str,
        message: str,
        registry: "Any",
        on_event: Optional[EventCallback],
    ) -> Dict[str, Any]:
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            # Prompt-cache fields from model_usage (Step 1). cache_read = tokens served
            # from the server-side prompt cache (~0.1x cost); cache_creation = tokens
            # written to it this turn (~1.25x). input_tokens is the uncached remainder.
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        text_parts: List[str] = []

        async def relay(etype: str, payload: Dict[str, Any]) -> None:
            if on_event:
                try:
                    await on_event(etype, payload)
                except Exception:
                    pass

        # Stream-first: open the stream, THEN send the kickoff message so no early
        # events are missed (managed-agents-events.md → Stream-first ordering).
        # `events.stream()` is an async method that RETURNS an AsyncStream — it must be
        # awaited first; `async with stream(...)` (wrapping the coroutine) raises and the
        # send below would never run, so the coordinator would never receive the message.
        stream = await self.client.beta.sessions.events.stream(session_id=session_id)
        async with stream:
            await self.client.beta.sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
            )

            async for event in stream:
                etype = getattr(event, "type", "")
                payload = _summarize_event(event)
                await relay(etype, payload)

                if etype == "agent.message":
                    text_parts.append(_content_text(getattr(event, "content", None)))

                elif etype == "agent.custom_tool_use":
                    output = await registry.route(
                        getattr(event, "name", ""), _coerce_input(getattr(event, "input", None))
                    )
                    result_event: Dict[str, Any] = {
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": getattr(event, "id", None),
                        "content": [{"type": "text", "text": _to_result_text(output)}],
                    }
                    # Multiagent: echo the originating sub-agent thread id.
                    thread_id = getattr(event, "session_thread_id", None)
                    if thread_id:
                        result_event["session_thread_id"] = thread_id
                    await self.client.beta.sessions.events.send(
                        session_id=session_id, events=[result_event]
                    )

                elif etype == "span.model_request_end":
                    mu = getattr(event, "model_usage", None)
                    if mu is not None:
                        usage["input_tokens"] += getattr(mu, "input_tokens", 0) or 0
                        usage["output_tokens"] += getattr(mu, "output_tokens", 0) or 0
                        usage["cache_read_input_tokens"] += (
                            getattr(mu, "cache_read_input_tokens", 0) or 0
                        )
                        usage["cache_creation_input_tokens"] += (
                            getattr(mu, "cache_creation_input_tokens", 0) or 0
                        )

                elif etype == "session.status_terminated":
                    break

                elif etype == "session.status_idle":
                    # Break only on a terminal stop reason. `requires_action` fires
                    # transiently while a tool result is pending — we've already sent it.
                    sr = getattr(event, "stop_reason", None)
                    if getattr(sr, "type", None) != "requires_action":
                        break

        # Skill loads (read/bash on a SKILL.md) only surface on the sub-agent THREAD
        # streams, never the session stream above — sweep them now and relay so the
        # console/UI can show which skills each specialist actually used this turn.
        await self._relay_skill_events(session_id, relay)

        return {"text": " ".join(p for p in text_parts if p), "usage": usage}

    async def _relay_skill_events(self, session_id: str, relay: EventCallback) -> None:
        """Walk each sub-agent thread's events and relay any skill-file access
        (a read/bash/glob touching a SKILL.md or /skills/ path). Deduped by event id
        across phases of the same session so a later turn doesn't re-emit earlier ones.
        Best-effort: any API hiccup is swallowed so it never breaks a workflow turn."""
        try:
            threads = []
            async for t in self.client.beta.sessions.threads.list(session_id):
                threads.append(t)
            # Per-sweep tallies so the logs show WHY skills did/didn't surface: which
            # threads ran, how many built-in tool calls each made, and how many we
            # resolved to a named skill load. Without this the sweep is a black box —
            # a 0 here could mean "agent never opened a skill" OR "detection missed it".
            n_tool_use = 0
            n_relayed = 0
            per_thread: List[str] = []
            for t in threads:
                tid = getattr(t, "id", None)
                if not tid:
                    continue
                agent = getattr(t, "agent", None)
                tname = getattr(agent, "name", None) if agent else None
                t_tools = 0
                t_skills = 0
                async for ev in self.client.beta.sessions.threads.events.list(
                    tid, session_id=session_id
                ):
                    if getattr(ev, "type", "") != "agent.tool_use":
                        continue
                    t_tools += 1
                    n_tool_use += 1
                    eid = getattr(ev, "id", None)
                    if not eid or eid in self._seen_skill_events:
                        continue
                    payload = _summarize_event(ev)
                    # Only relay loads we could resolve to a skill NAME — drops bare
                    # `glob /skills` scans so the count reflects distinct skills used.
                    if payload.get("category") != "skill" or not payload.get("skill"):
                        continue
                    self._seen_skill_events.add(eid)
                    if tname:
                        payload["agent_name"] = tname
                    t_skills += 1
                    n_relayed += 1
                    await relay("agent.tool_use", payload)
                per_thread.append(f"{tname or tid}: tools={t_tools} skills={t_skills}")
            log.info(
                "skill_sweep.done",
                session_id=session_id,
                threads=len(threads),
                tool_use_events=n_tool_use,
                skills_relayed=n_relayed,
                detail=per_thread,
            )
        except Exception:
            log.warning("skill_sweep.failed", session_id=session_id, exc_info=True)
