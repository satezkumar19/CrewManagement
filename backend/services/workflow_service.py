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
        """
        Drive Phase 2 with a retry loop:

        - Try the user-picked candidate first.
        - On compliance "failed", record the rejection, advance to the next
          candidate from the Phase 1 ranked list (skipping anyone already tried),
          and retry — up to max_compliance_retries.
        - On "passed" / "warning" the workflow completes.
        - If the queue is exhausted, the workflow fails.

        The master agent emits a `compliance_phase_complete` event per attempt
        but does NOT decide the terminal workflow state; that's owned here.
        """
        try:
            queue = self._build_candidate_queue(workflow, candidate)
            master = MasterAgent(event_callback=self._event_callback)
            attempt = 0

            # attempt 0 is the initial try; attempts 1..max_compliance_retries
            # are the retries. With max=3 → up to 4 total attempts.
            for current in queue:
                if attempt > workflow.max_compliance_retries:
                    break

                workflow.compliance_retries = attempt
                updated = await master.orchestrate_compliance(workflow, current, port)
                workflow = updated  # master returns the same instance, but be explicit

                report = (workflow.compliance_result or {}).get("compliance_report") or {}
                overall = (report.get("overall_status") or "").lower()

                if overall in ("passed", "warning"):
                    await self._finish_compliance_success(workflow, current, overall)
                    return

                # Failed — record, decide whether to retry
                rejection = {
                    "attempt": attempt + 1,
                    "candidate": current,
                    "compliance_score": report.get("compliance_score"),
                    "reason": report.get("recommendation", "Compliance failed"),
                    "failures": report.get("failures", []),
                }
                workflow.rejected_candidates.append(rejection)

                remaining = queue[attempt + 1 :]
                next_attempt_within_budget = (attempt + 1) <= workflow.max_compliance_retries
                next_candidate = remaining[0] if remaining and next_attempt_within_budget else None

                if next_candidate is None:
                    await self._finish_compliance_exhausted(workflow, rejection)
                    return

                workflow.status = WorkflowStatus.RETRYING_COMPLIANCE
                await state_service.update_workflow(workflow)
                await self._event_callback("compliance_retry", "Master Agent", {
                    "workflow_id": workflow.workflow_id,
                    "attempt": attempt + 1,
                    "max_retries": workflow.max_compliance_retries,
                    "rejected": current,
                    "reason": rejection["reason"],
                    "next_candidate": next_candidate,
                })
                attempt += 1

            # Loop fell through without a success or an early exhaustion exit —
            # means the for-loop ran out of queue items but we still had budget.
            last = workflow.rejected_candidates[-1] if workflow.rejected_candidates else None
            await self._finish_compliance_exhausted(workflow, last)

        except Exception as exc:
            log.error("compliance.orchestration.error", error=str(exc))
            workflow.status = WorkflowStatus.FAILED
            await state_service.update_workflow(workflow)
            await self._event_callback("workflow_failed", "Master Agent", {
                "workflow_id": workflow.workflow_id,
                "error": str(exc),
            })

    def _build_candidate_queue(
        self, workflow: WorkflowState, first_candidate: Dict[str, Any]
    ) -> list:
        """
        Build the ordered try-list for compliance: the user-picked candidate first,
        then the rest of the Phase 1 ranked list in score order, deduped on crew_id.
        """
        first_id = first_candidate.get("crew_id")
        ranked = ((workflow.crew_match_result or {}).get("ranked_candidates") or [])
        queue = [first_candidate]
        seen = {first_id}
        for c in ranked:
            cid = c.get("crew_id")
            if cid and cid not in seen:
                queue.append(c)
                seen.add(cid)
        return queue

    async def _finish_compliance_success(
        self, workflow: WorkflowState, candidate: Dict[str, Any], overall: str
    ) -> None:
        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.utcnow()
        # Reflect the actually-signed-on candidate, which may differ from the
        # operator's initial pick when retries fired.
        workflow.matched_crew = candidate
        workflow.matched_crew_id = candidate.get("crew_id")
        await state_service.update_workflow(workflow)
        await self._event_callback("workflow_completed", "Master Agent", {
            "workflow_id": workflow.workflow_id,
            "compliance_status": overall,
            "final_candidate": candidate,
            "retries": workflow.compliance_retries,
            "rejected_candidates": workflow.rejected_candidates,
            "total_tokens": workflow.total_tokens,
            "total_cost": workflow.total_cost,
        })
        log.info(
            "compliance.orchestration.complete",
            workflow_id=workflow.workflow_id,
            retries=workflow.compliance_retries,
        )

    async def _finish_compliance_exhausted(
        self, workflow: WorkflowState, last_rejection: Optional[Dict[str, Any]]
    ) -> None:
        workflow.status = WorkflowStatus.FAILED
        workflow.completed_at = datetime.utcnow()
        await state_service.update_workflow(workflow)
        await self._event_callback("workflow_failed", "Master Agent", {
            "workflow_id": workflow.workflow_id,
            "error": "Compliance retry budget exhausted",
            "attempts": len(workflow.rejected_candidates),
            "rejected_candidates": workflow.rejected_candidates,
            "last_rejection": last_rejection,
        })
        log.warning(
            "compliance.orchestration.exhausted",
            workflow_id=workflow.workflow_id,
            attempts=len(workflow.rejected_candidates),
        )

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
