"""
Workflow Service — orchestrates agent execution and state transitions.
Integrates Master Agent with state management and WebSocket events.
"""
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import structlog

from agents.master_agent import MasterAgent
from database.models import WorkflowState, WorkflowStatus
from database.crew_repository import get_crew_by_id, get_sign_on_crew, update_crew
from services.state_service import state_service

log = structlog.get_logger()


class WorkflowService:
    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self.broadcast_fn = broadcast_fn

    async def _event_callback(self, event_type: str, agent_name: str, data: Dict[str, Any]):
        """Relay agent events → WebSocket broadcast."""
        if self.broadcast_fn:
            await self.broadcast_fn({
                "event_type": event_type,
                "agent_name": agent_name,
                "data": data,
                "timestamp": datetime.utcnow().isoformat(),
            })

    async def initiate_sign_off(
        self,
        crew_id: str,
        reason: str = "Contract completion",
    ) -> WorkflowState:
        """
        Trigger the full sign-off workflow.
        Returns immediately after creating the workflow; orchestration runs async.
        """
        # Look up the sign-off crew member
        crew = await get_crew_by_id(crew_id, pool="signoff")
        if not crew:
            raise ValueError(f"Crew member {crew_id} not found in sign-off pool")

        # Create workflow record
        workflow = WorkflowState(
            trigger=f"Sign-off initiated for {crew['name']} ({crew_id})",
            sign_off_crew_id=crew_id,
            sign_off_crew=crew,
            memory={
                "short_term": {
                    "initiated_at": datetime.utcnow().isoformat(),
                    "reason": reason,
                    "sign_off_crew": crew,
                },
                "long_term": {
                    "previous_operations": [],
                },
            },
        )

        await state_service.create_workflow(workflow)

        # Broadcast workflow created
        await self._event_callback("workflow_created", "Master Agent", {
            "workflow_id": workflow.workflow_id,
            "crew_name": crew["name"],
            "rank": crew["rank"],
            "vessel": crew["vessel"],
        })

        # Run orchestration in background
        asyncio.create_task(self._run_sign_off_orchestration(workflow, crew))

        return workflow

    async def _run_sign_off_orchestration(
        self, workflow: WorkflowState, sign_off_crew: Dict[str, Any]
    ):
        try:
            master = MasterAgent(event_callback=self._event_callback)
            updated = await master.orchestrate_sign_off(workflow, sign_off_crew)
            await state_service.update_workflow(updated)

            # Persist the sign-off outcome to the crew table: the departing crew
            # member leaves the onboard (signoff) pool and becomes available for
            # sign-on (signon pool).
            crew_id = updated.sign_off_crew_id
            if crew_id:
                row = await update_crew(crew_id, pool="signon", status="Signed Off")
                if row:
                    log.info("sign_off.crew_pool_updated", crew_id=crew_id, pool="signon")
                    await self._event_callback("crew_updated", "Master Agent", {
                        "workflow_id": updated.workflow_id,
                        "crew_id": crew_id,
                        "pool": "signon",
                        "status": "Signed Off",
                    })
                else:
                    log.warning("sign_off.crew_not_found_for_update", crew_id=crew_id)

            log.info("sign_off.orchestration.complete", workflow_id=workflow.workflow_id)
        except Exception as exc:
            log.error("sign_off.orchestration.error", error=str(exc))
            workflow.status = WorkflowStatus.FAILED
            await state_service.update_workflow(workflow)
            await self._event_callback("workflow_failed", "Master Agent", {
                "workflow_id": workflow.workflow_id,
                "error": str(exc),
            })

    async def initiate_sign_on(
        self,
        workflow_id: str,
        candidate_crew_id: str,
    ) -> WorkflowState:
        """
        Triggered when user clicks 'Sign On' for the matched candidate.
        Activates the Compliance Agent.
        """
        workflow = await state_service.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get candidate profile (could be from sign-on pool or matched crew)
        candidate = await get_crew_by_id(candidate_crew_id, pool="signon")
        if not candidate:
            # Try the matched crew data
            candidate = workflow.matched_crew
        if not candidate:
            raise ValueError(f"Candidate {candidate_crew_id} not found")

        port = workflow.sign_off_crew.get("port", "Singapore") if workflow.sign_off_crew else "Singapore"

        # Update memory with sign-on context
        if workflow.memory.get("short_term"):
            workflow.memory["short_term"]["sign_on_candidate"] = candidate
            workflow.memory["short_term"]["sign_on_initiated_at"] = datetime.utcnow().isoformat()

        await state_service.update_workflow(workflow)

        await self._event_callback("sign_on_initiated", "Master Agent", {
            "workflow_id": workflow_id,
            "candidate_name": candidate.get("name"),
            "candidate_id": candidate_crew_id,
        })

        # Run compliance orchestration in background
        asyncio.create_task(
            self._run_compliance_orchestration(workflow, candidate, port)
        )

        return workflow

    async def _run_compliance_orchestration(
        self,
        workflow: WorkflowState,
        candidate: Dict[str, Any],
        port: str,
    ):
        try:
            master = MasterAgent(event_callback=self._event_callback)
            updated = await master.orchestrate_compliance(workflow, candidate, port)
            await state_service.update_workflow(updated)
            log.info("compliance.orchestration.complete", workflow_id=workflow.workflow_id)
        except Exception as exc:
            log.error("compliance.orchestration.error", error=str(exc))
            workflow.status = WorkflowStatus.FAILED
            await state_service.update_workflow(workflow)
            await self._event_callback("workflow_failed", "Master Agent", {
                "workflow_id": workflow.workflow_id,
                "error": str(exc),
            })

    async def pause_workflow(self, workflow_id: str) -> WorkflowState:
        workflow = await state_service.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow.status = WorkflowStatus.PAUSED
        await self._event_callback("workflow_paused", "Master Agent", {"workflow_id": workflow_id})
        return await state_service.update_workflow(workflow)

    async def resume_workflow(self, workflow_id: str) -> WorkflowState:
        workflow = await state_service.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow.status = WorkflowStatus.RUNNING
        await self._event_callback("workflow_resumed", "Master Agent", {"workflow_id": workflow_id})
        return await state_service.update_workflow(workflow)

    async def cancel_workflow(self, workflow_id: str) -> WorkflowState:
        workflow = await state_service.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow.status = WorkflowStatus.CANCELLED
        await self._event_callback("workflow_cancelled", "Master Agent", {"workflow_id": workflow_id})
        return await state_service.update_workflow(workflow)
