"""
Master Agent — coordinator-session orchestrator.

Under Managed Agents the "master" is no longer a Claude loop in our process. The
persisted **coordinator agent** (see registry.COORDINATOR_SYSTEM_ROLE) runs on
Anthropic's orchestration layer and natively delegates to the four specialist
sub-agents. This class drives ONE coordinator session across the two workflow
phases, translating session-stream events onto our WebSocket vocabulary and
extracting structured results via the specialist registry.

Phase 1 (sign-off): crew_matching + travel + notification, then pause (session idle).
Phase 2 (sign-on):  compliance + notification, on the SAME session.
"""
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import structlog

from agents.base_agent import COST_PER_INPUT_TOKEN, COST_PER_OUTPUT_TOKEN
from agents.managed.client import ManagedAgentsClient
from agents.managed.registry import PHASE1_KEYS, PHASE2_KEYS, SpecialistRegistry
from database.models import WorkflowState, WorkflowStatus

log = structlog.get_logger()

# Managed session-event type → our existing WebSocket event vocabulary, so the
# frontend keeps receiving familiar event names. Unmapped types pass through raw.
# session.thread_status_idle is handled specially in _make_on_event (terminal-only).
_EVENT_TYPE_MAP = {
    "session.thread_created": "agent_started",
    "session.thread_status_running": "agent_started",
    "session.thread_status_terminated": "agent_completed",
    "agent.message": "agent_message",
    "agent.thinking": "agent_thinking",
    "agent.custom_tool_use": "agent_tool_use",
    "span.model_request_end": "model_usage",
}


class MasterAgent:
    """Drives the coordinator session for one workflow across both phases."""

    def __init__(self, event_callback: Optional[Callable] = None):
        self.name = "Master Agent"
        self.event_callback = event_callback
        self.client = ManagedAgentsClient()

    # ── Phase 1: sign-off ───────────────────────────────────────────────────────

    async def orchestrate_sign_off(
        self, workflow: WorkflowState, sign_off_crew: Dict[str, Any]
    ) -> WorkflowState:
        await self._emit_timeline(workflow, "Master Agent activated — opening coordinator session")
        workflow.status = WorkflowStatus.RUNNING

        if not workflow.session_id:
            workflow.session_id = await self.client.create_session(
                title=f"Sign-off: {sign_off_crew.get('name')} ({workflow.workflow_id[:8]})"
            )
        await self._emit("master_routing", {
            "workflow_id": workflow.workflow_id,
            "session_id": workflow.session_id,
            "action": "Delegating to Crew Matching + Travel + Notification (parallel)",
            "sign_off_crew": sign_off_crew.get("name"),
        })

        registry = SpecialistRegistry(PHASE1_KEYS, event_callback=self.event_callback)
        await self._emit_timeline(workflow, "Dispatching specialists: Crew Matching, Travel, Notification")

        turn = await self.client.run_turn(
            session_id=workflow.session_id,
            message=self._phase1_prompt(sign_off_crew),
            registry=registry,
            on_event=self._make_on_event(workflow),
        )

        results = await registry.finalize(turn["text"], {"workflow_id": workflow.workflow_id})
        workflow.crew_match_result = results.get("crew_matching")
        workflow.travel_result = results.get("travel")
        workflow.notification_result = results.get("notification")

        crew_result = workflow.crew_match_result or {}
        if crew_result.get("top_match"):
            workflow.matched_crew = crew_result["top_match"]
            workflow.matched_crew_id = crew_result["top_match"].get("crew_id")

        self._record_executions(workflow, registry)
        self._record_usage(workflow, turn["usage"])

        await self._emit_timeline(workflow, "Phase 1 complete — crew matched, travel arranged, captain notified")
        workflow.status = WorkflowStatus.WAITING
        await self._emit("master_waiting", {
            "workflow_id": workflow.workflow_id,
            "matched_crew": workflow.matched_crew,
            "message": "Waiting for user to confirm sign-on",
        })
        return workflow

    # ── Phase 2: sign-on / compliance ────────────────────────────────────────────

    async def orchestrate_compliance(
        self, workflow: WorkflowState, candidate_crew: Dict[str, Any], port: str
    ) -> WorkflowState:
        if not workflow.session_id:
            # Defensive: should have been created in Phase 1.
            workflow.session_id = await self.client.create_session(
                title=f"Sign-on: {candidate_crew.get('name')} ({workflow.workflow_id[:8]})"
            )

        await self._emit_timeline(workflow, f"Compliance check triggered for {candidate_crew.get('name')}")
        workflow.status = WorkflowStatus.RUNNING
        await self._emit("master_routing", {
            "workflow_id": workflow.workflow_id,
            "session_id": workflow.session_id,
            "action": "Delegating to Compliance Agent",
            "candidate": candidate_crew.get("name"),
        })

        registry = SpecialistRegistry(PHASE2_KEYS, event_callback=self.event_callback)
        turn = await self.client.run_turn(
            session_id=workflow.session_id,
            message=self._phase2_prompt(candidate_crew, port),
            registry=registry,
            on_event=self._make_on_event(workflow),
        )

        results = await registry.finalize(turn["text"], {"workflow_id": workflow.workflow_id, "port": port})
        workflow.compliance_result = results.get("compliance")

        self._record_executions(workflow, registry)
        self._record_usage(workflow, turn["usage"])

        report = (workflow.compliance_result or {}).get("compliance_report") or {}
        overall = report.get("overall_status", "unknown")

        # Phase-level signal only — whether the workflow is COMPLETED, retrying,
        # or FAILED is decided by the service layer, which owns the retry loop.
        await self._emit_timeline(workflow, f"Compliance {overall} for {candidate_crew.get('name')}")
        await self._emit("compliance_phase_complete", {
            "workflow_id": workflow.workflow_id,
            "candidate": candidate_crew,
            "compliance_status": overall,
        })
        return workflow

    # ── Prompts ──────────────────────────────────────────────────────────────────

    def _phase1_prompt(self, c: Dict[str, Any]) -> str:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return (
            "Sign-off request received. Process PHASE 1 for the departing crew member, then STOP "
            "(do not run a compliance check — wait for the user to confirm the sign-on).\n\n"
            "Departing crew member:\n"
            f"- Name: {c.get('name')}\n"
            f"- Rank: {c.get('rank')}\n"
            f"- Grade: {c.get('grade')}\n"
            f"- Vessel: {c.get('vessel')}\n"
            f"- Port: {c.get('port')}\n"
            f"- Nationality: {c.get('nationality')}\n"
            f"- Sign-off date: {today}\n\n"
            "Delegate IN PARALLEL:\n"
            "1. crew_matching — search, rank, and select the best available replacement candidate.\n"
            "2. travel — generate the flight ticket, port clearance, and travel summary for the departing crew.\n"
            "3. notification — notify the Captain (sign-off initiated), the Shore Manager (operational "
            "update), and the departing crew member (farewell + travel info); note a replacement has been "
            "requested and will be announced shortly.\n\n"
            "When all three report back, summarize the replacement candidate, the travel package, and the "
            "notifications sent."
        )

    def _phase2_prompt(self, c: Dict[str, Any], port: str) -> str:
        return (
            "The user has CONFIRMED the sign-on for the following candidate. Process PHASE 2.\n\n"
            "Incoming crew member:\n"
            f"- Name: {c.get('name')}\n"
            f"- Crew ID: {c.get('crew_id')}\n"
            f"- Rank: {c.get('rank')}\n"
            f"- Nationality: {c.get('nationality')}\n"
            f"- Passport Expiry: {c.get('passport_expiry', 'Unknown')}\n"
            f"- Medical Expiry: {c.get('medical_expiry', 'Unknown')}\n"
            f"- STCW Status: {c.get('stcw_status', 'Unknown')}\n"
            f"- Visa Status: {c.get('visa_status', 'Unknown')}\n"
            f"- Certifications: {c.get('certifications', [])}\n"
            f"- Boarding Port: {port}\n\n"
            "Delegate:\n"
            "1. compliance — validate all documents and port restrictions, then produce the final "
            "compliance report (overall status + score + recommendation).\n"
            "2. notification — notify the Captain and Shore Manager of the compliance outcome and the "
            "final sign-on decision.\n\n"
            "Summarize the compliance verdict and stop."
        )

    # ── Event relay + bookkeeping ──────────────────────────────────────────────────

    def _make_on_event(self, workflow: WorkflowState) -> Callable:
        async def on_event(etype: str, payload: Dict[str, Any]) -> None:
            # Attribute each relayed event to the agent it actually came from
            # (sub-agent threads carry their own agent_name) — not to "Master Agent".
            agent_name = payload.get("agent_name") or self.name
            data = {"workflow_id": workflow.workflow_id, **payload}
            if etype == "session.thread_status_idle":
                # A thread goes idle transiently with `requires_action` while it waits
                # on a custom-tool result; only treat a terminal stop as "completed".
                if payload.get("stop_reason") != "requires_action":
                    await self._emit("agent_completed", data, agent_name=agent_name)
                return
            mapped = _EVENT_TYPE_MAP.get(etype, etype)
            await self._emit(mapped, data, agent_name=agent_name)
        return on_event

    def _record_executions(self, workflow: WorkflowState, registry: SpecialistRegistry) -> None:
        for agent in registry.agents.values():
            workflow.agent_executions.append(agent.execution)
            workflow.total_tokens += agent.execution.tokens_used
            workflow.total_cost += agent.execution.estimated_cost

    def _record_usage(self, workflow: WorkflowState, usage: Dict[str, int]) -> None:
        # Turn-level usage covers the coordinator + all sub-agent threads. We can't
        # cleanly attribute it per specialist from the stream, so it accrues at the
        # workflow level (per-agent token columns therefore read 0 — by design).
        workflow.total_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        workflow.total_cost += (
            usage.get("input_tokens", 0) * COST_PER_INPUT_TOKEN
            + usage.get("output_tokens", 0) * COST_PER_OUTPUT_TOKEN
        )

    async def _emit_timeline(self, workflow: WorkflowState, event: str) -> None:
        entry = {"timestamp": datetime.utcnow().isoformat(), "event": event, "agent": self.name}
        workflow.timeline.append(entry)
        workflow.updated_at = datetime.utcnow()
        await self._emit("timeline_update", {"workflow_id": workflow.workflow_id, "entry": entry})

    async def _emit(self, event_type: str, data: Dict[str, Any], agent_name: str = None) -> None:
        if self.event_callback:
            try:
                await self.event_callback(
                    event_type=event_type, agent_name=agent_name or self.name, data=data
                )
            except Exception:
                pass
