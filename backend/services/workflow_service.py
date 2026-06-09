"""
Workflow Service — orchestrates agent execution and state transitions.
Integrates Master Agent with state management and WebSocket events.
"""
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import structlog

from agents.master_agent import MasterAgent
from config import settings
from database.models import WorkflowState, WorkflowStatus
from database.crew_repository import get_crew_by_id, get_sign_on_crew, update_crew
from database.decision_repository import get_decision
from services.state_service import state_service
from services.decision_trace_service import decision_trace_service
from services.precedent_service import precedent_service

log = structlog.get_logger()

# L4 #4 — how many ranked candidates the rejection-retry loop will try (top match
# + up to 2 fallbacks) before recording a final rejection.
MAX_SIGNON_ATTEMPTS = 3


class WorkflowService:
    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self.broadcast_fn = broadcast_fn

    async def _event_callback(self, event_type: str, agent_name: str, data: Dict[str, Any]):
        """Relay agent events → WebSocket broadcast, and feed them to L2 OpsMap.

        Every event that flows through here is the raw material the OpsMap dimension
        mines into a process graph (see L2Knowledge_graph.ops_map). We record it
        keyed by the workflow_id (the process-mining 'case id'). Wrapped in a
        best-effort try/except so process-mining capture can never break the live
        workflow or the WebSocket stream.
        """
        ts = datetime.utcnow().isoformat()
        try:
            from L2Knowledge_graph.ops_map import record_event
            record_event((data or {}).get("workflow_id"), event_type, agent_name, ts, data)
        except Exception as exc:  # pragma: no cover - capture must never be fatal
            log.warning("opsmap.record_failed", event_type=event_type, error=str(exc))

        if self.broadcast_fn:
            await self.broadcast_fn({
                "event_type": event_type,
                "agent_name": agent_name,
                "data": data,
                "timestamp": ts,
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
            # L4 #2: consult the Precedent Index at the START of the matching query.
            # On the 2nd+ sign-off for the same vacancy profile (rank/grade/port)
            # this returns prior placements. Stashed on the workflow so the captured
            # decision records what the lookup returned.
            precedent = await precedent_service.consult(
                rank=sign_off_crew.get("rank"),
                grade=sign_off_crew.get("grade"),
                port=sign_off_crew.get("port"),
                nationality=sign_off_crew.get("nationality"),
                broadcast=self._event_callback,
                workflow_id=workflow.workflow_id,
            )
            workflow.memory.setdefault("short_term", {})["precedent"] = precedent

            master = MasterAgent(event_callback=self._event_callback)
            updated = await master.orchestrate_sign_off(workflow, sign_off_crew, auto_proceed=True)
            await state_service.update_workflow(updated)

            # L4: capture the placement decision L3 just produced (matched crew +
            # ranked alternatives + agent trajectory) as a persisted Decision trace.
            # Read-only consumer of WorkflowState; best-effort (never raises).
            await decision_trace_service.capture(updated, broadcast=self._event_callback)

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

            # Auto-chain: validate the matched replacement's documents via Compliance,
            # and on a pass/warning add them to the onboard (signoff) pool so they
            # appear in the Sign-Off tab.
            await self._auto_compliance_and_signon(master, updated, sign_off_crew)

            log.info("sign_off.orchestration.complete", workflow_id=workflow.workflow_id)
        except Exception as exc:
            log.error("sign_off.orchestration.error", error=str(exc))
            workflow.status = WorkflowStatus.FAILED
            await state_service.update_workflow(workflow)
            await self._event_callback("workflow_failed", "Master Agent", {
                "workflow_id": workflow.workflow_id,
                "error": str(exc),
            })

    async def _auto_compliance_and_signon(
        self, master: MasterAgent, workflow: WorkflowState, sign_off_crew: Dict[str, Any]
    ) -> None:
        """After matching, run Compliance on the ranked candidates IN ORDER until one
        clears (L4 #4 rejection-retry loop). The top match is tried first; on a
        compliance failure the next-best ranked alternative (which already carries
        the Phase-3 precedent boost) is tried, up to MAX_SIGNON_ATTEMPTS. The first
        pass/warning signs that crew on; only when every attempt fails is a final
        rejection recorded. Runs on the SAME coordinator session as Phase 1.
        """
        # Candidates to try, best-first. ranked_candidates is the boosted/sorted list;
        # fall back to the single top match if it's absent.
        ranked = (workflow.crew_match_result or {}).get("ranked_candidates") or []
        if not ranked and workflow.matched_crew:
            ranked = [workflow.matched_crew]
        queue = ranked[:MAX_SIGNON_ATTEMPTS]
        if not queue:
            log.warning("auto_compliance.no_match", workflow_id=workflow.workflow_id)
            return

        port = (sign_off_crew or {}).get("port", "Singapore")
        attempts: list = []
        winner: Optional[Dict[str, Any]] = None  # {profile, status, score, warnings, recommendation, subgraph}
        # HITL — set when the loop hits a case the system shouldn't resolve itself (a
        # conditional 'warning' pass with review-on-warning enabled). Routes to a human.
        review_request: Optional[Dict[str, Any]] = None

        for idx, cand in enumerate(queue):
            cid = cand.get("crew_id")
            # Full document set (passport/medical/visa/STCW) lives on the signon-pool row.
            profile = await get_crew_by_id(cid, pool="signon")
            if not profile and cid == (workflow.matched_crew or {}).get("crew_id"):
                profile = dict(workflow.matched_crew or {})
            if not profile:
                log.warning("auto_compliance.candidate_not_found", crew_id=cid)
                continue

            is_retry = idx > 0
            await self._event_callback("auto_compliance", "Master Agent", {
                "workflow_id": workflow.workflow_id,
                "candidate_id": cid,
                "candidate_name": profile.get("name"),
                "candidate_rank": profile.get("rank"),
                "match_confidence": cand.get("confidence_score"),
                "match_reasons": cand.get("match_reasons", []),
                "attempt": idx + 1,
                "is_retry": is_retry,
                "message": (
                    (f"Retry {idx + 1}/{len(queue)}: validating next-best candidate "
                     f"{profile.get('name')} after a compliance rejection")
                    if is_retry else
                    f"Sharing {profile.get('name')}'s documents with Compliance for validation"
                ),
            })

            # Persist an in-progress ("checking") attempt BEFORE running compliance so a
            # client that lands on the Decisions tab mid-run sees this candidate being
            # validated rather than a stale lone pending node. Overwritten in place with
            # the verdict below.
            attempts.append({
                "order": idx + 1,
                "crew_id": cid,
                "name": profile.get("name"),
                "rank": profile.get("rank"),
                "compliance_status": "checking",
                "compliance_score": None,
                "failures": [],
                "warnings": [],
            })
            await decision_trace_service.record_progress(
                workflow.workflow_id,
                attempts=attempts,
                pending_reason=f"Validating {profile.get('name')}'s documents (attempt {idx + 1})…",
            )

            updated = await master.orchestrate_compliance(workflow, profile, port)
            await state_service.update_workflow(updated)

            report = (updated.compliance_result or {}).get("compliance_report") or {}
            # The context subgraph the Compliance Agent reasoned over — streamed to the UI.
            subgraph = (updated.compliance_result or {}).get("compliance_subgraph")
            status = report.get("overall_status", "unknown")
            score = report.get("compliance_score")
            warnings = report.get("warnings", []) or []
            failures = report.get("failures", []) or []
            recommendation = report.get("recommendation")

            # Replace the in-progress placeholder with the actual compliance verdict.
            attempts[-1] = {
                "order": idx + 1,
                "crew_id": cid,
                "name": profile.get("name"),
                "rank": profile.get("rank"),
                "compliance_status": status,
                "compliance_score": score,
                "failures": failures,
                "warnings": warnings,
            }

            # Pass rule: a clean 'passed' always auto-signs-on. A 'warning' (conditional)
            # also auto-signs-on UNLESS HITL review-on-warning is enabled — then the
            # system stops short of deciding the exception itself and hands it to a human.
            review_on_warning = settings.hitl_enabled and settings.hitl_review_on_warning
            if status == "passed" or (status == "warning" and not review_on_warning):
                winner = {
                    "profile": profile, "status": status, "score": score,
                    "warnings": warnings, "recommendation": recommendation, "subgraph": subgraph,
                }
                break
            if status == "warning":
                # Conditional pass → a human decides. Stop the retry loop; the caller
                # requests review for this candidate (see below).
                review_request = {
                    "profile": profile, "status": status, "score": score,
                    "warnings": warnings, "failures": failures,
                    "recommendation": recommendation, "subgraph": subgraph,
                    "trigger": "warning",
                }
                break

            # Failed this candidate. Persist the rejection (still pending overall) so the
            # DB reflects it for a mid-run reader; if alternatives remain, announce the
            # retry — otherwise fall through to the final rejection below.
            log.info("auto_compliance.attempt_rejected", crew_id=cid, status=status, attempt=idx + 1)
            more = idx < len(queue) - 1
            await decision_trace_service.record_progress(
                workflow.workflow_id,
                attempts=attempts,
                pending_reason=(
                    f"{profile.get('name')} failed compliance ({status}); "
                    + ("selecting the next-best candidate…" if more else "no candidates remain.")
                ),
            )
            if more:
                await self._event_callback("sign_on_attempt_rejected", "Compliance Agent", {
                    "workflow_id": workflow.workflow_id,
                    "crew_id": cid,
                    "crew_name": profile.get("name"),
                    "crew_rank": profile.get("rank"),
                    "compliance_status": status,
                    "compliance_score": score,
                    "failures": failures,
                    "attempt": idx + 1,
                    "subgraph": subgraph,
                    "message": (
                        f"{profile.get('name')} did not clear compliance ({status}) — "
                        f"retrying with the next-best candidate"
                    ),
                })

        if winner is not None:
            profile = winner["profile"]
            cid = profile.get("crew_id")
            chosen_crew = {
                "crew_id": cid, "name": profile.get("name"), "rank": profile.get("rank"),
                "grade": profile.get("grade"), "port": profile.get("port"),
                "nationality": profile.get("nationality"),
            }
            # L4: stamp the decision's outcome (closes the trace's loop) with the
            # full attempt journey, overriding the chosen crew when a FALLBACK won;
            # then append the completed placement to the Precedent Index (#2).
            updated_decision = await decision_trace_service.record_outcome(
                workflow.workflow_id,
                outcome_status="signed_on",
                compliance_status=winner["status"],
                compliance_score=winner["score"],
                outcome_reasons=winner["warnings"],
                attempts=attempts,
                chosen_crew=chosen_crew,
                chosen_crew_id=cid,
                broadcast=self._event_callback,
            )
            if updated_decision:
                await precedent_service.record_placement(updated_decision)
            row = await update_crew(cid, pool="signoff", status="Onboard")
            if row:
                retried = len(attempts) > 1
                log.info("auto_compliance.signed_on", crew_id=cid, status=winner["status"], attempts=len(attempts))
                await self._event_callback("crew_signed_on", "Compliance Agent", {
                    "workflow_id": workflow.workflow_id,
                    "crew_id": cid,
                    "crew_name": profile.get("name"),
                    "crew_rank": profile.get("rank"),
                    "compliance_status": winner["status"],
                    "compliance_score": winner["score"],
                    "warnings": winner["warnings"],
                    "recommendation": winner["recommendation"],
                    "subgraph": winner["subgraph"],
                    "attempts": attempts,
                    "message": (
                        f"{profile.get('name')} cleared compliance "
                        f"({winner['status']}, {winner['score']}%)"
                        + (f" on attempt {len(attempts)}" if retried else "")
                        + " — added to onboard crew (Sign-Off tab)"
                    ),
                })
            else:
                log.warning("auto_compliance.signon_crew_not_found", crew_id=cid)
            return

        # HITL — the system declines to resolve this itself: a conditional 'warning'
        # candidate (set above), or (when review-on-exhausted is enabled) every
        # candidate failed. Hand it to a human instead of auto-deciding.
        if review_request is None and (
            settings.hitl_enabled and settings.hitl_review_on_exhausted and attempts
        ):
            review_request = {
                "profile": None,  # no single proposal; the best near-miss is offered
                "status": (attempts[-1] or {}).get("compliance_status"),
                "score": (attempts[-1] or {}).get("compliance_score"),
                "warnings": [],
                "failures": list((attempts[-1] or {}).get("failures") or []),
                "recommendation": None,
                "subgraph": None,
                "trigger": "exhausted",
            }
        if review_request is not None:
            await self._request_human_review(workflow, review_request, attempts)
            return

        # Every attempt failed (and HITL review-on-exhausted is off) — final rejection.
        last = attempts[-1] if attempts else {}
        log.info("auto_compliance.rejected", workflow_id=workflow.workflow_id, attempts=len(attempts))
        exhausted = len(attempts) > 1
        outcome_reasons = list(last.get("failures") or [])
        if exhausted:
            outcome_reasons = [f"All {len(attempts)} candidates failed compliance"] + outcome_reasons
        updated_decision = await decision_trace_service.record_outcome(
            workflow.workflow_id,
            outcome_status="rejected",
            compliance_status=last.get("compliance_status"),
            compliance_score=last.get("compliance_score"),
            outcome_reasons=outcome_reasons,
            attempts=attempts,
            broadcast=self._event_callback,
        )
        if updated_decision:
            await precedent_service.record_placement(updated_decision)
        await self._event_callback("sign_on_rejected", "Compliance Agent", {
            "workflow_id": workflow.workflow_id,
            "crew_id": last.get("crew_id"),
            "crew_name": last.get("name"),
            "crew_rank": last.get("rank"),
            "compliance_status": last.get("compliance_status"),
            "compliance_score": last.get("compliance_score"),
            "failures": last.get("failures") or [],
            "attempts": attempts,
            "message": (
                f"No candidate cleared compliance after {len(attempts)} attempt(s) — not signed on"
                if exhausted else
                f"{last.get('name')} did not clear compliance "
                f"({last.get('compliance_status')}) — not signed on"
            ),
        })

    async def _request_human_review(
        self, workflow: WorkflowState, req: Dict[str, Any], attempts: list
    ) -> None:
        """Pause the workflow for a human decision (HITL) and announce it live.

        Marks the decision review_status='pending_review', sets the workflow to
        WAITING, and broadcasts `review_requested` with the candidate context so the
        Decision Tab can surface the review queue.
        """
        trigger = req.get("trigger")
        profile = req.get("profile")
        if not profile and attempts:
            # Exhausted: offer the best near-miss (highest compliance score) as the
            # candidate under review; the human may override to anyone.
            best = max(attempts, key=lambda a: (a.get("compliance_score") or 0))
            profile = {
                "crew_id": best.get("crew_id"), "name": best.get("name"), "rank": best.get("rank"),
            }
        profile = profile or {}
        cid = profile.get("crew_id")
        proposal = {
            "crew_id": cid, "name": profile.get("name"), "rank": profile.get("rank"),
            "grade": profile.get("grade"), "port": profile.get("port"),
            "nationality": profile.get("nationality"),
            "compliance_status": req.get("status"), "compliance_score": req.get("score"),
            "trigger": trigger,
        }
        if trigger == "warning":
            pending_reason = (
                f"{profile.get('name')} cleared compliance only conditionally "
                f"({req.get('status')}, {req.get('score')}%) — awaiting a human decision to "
                f"approve the exception, reject, or pick another candidate."
            )
        else:  # exhausted
            pending_reason = (
                f"All {len(attempts)} candidate(s) failed compliance — awaiting a human "
                f"decision to override-approve a candidate or confirm the rejection."
            )
        reasons = list(req.get("warnings") or req.get("failures") or [])

        updated = await decision_trace_service.request_review(
            workflow.workflow_id,
            review_trigger=trigger or "review",
            pending_reason=pending_reason,
            ai_proposal=proposal,
            attempts=attempts,
            compliance_status=req.get("status"),
            compliance_score=req.get("score"),
            outcome_reasons=reasons,
        )

        workflow.status = WorkflowStatus.WAITING
        await state_service.update_workflow(workflow)
        log.info("auto_compliance.review_requested", workflow_id=workflow.workflow_id, trigger=trigger)

        await self._event_callback("review_requested", "Compliance Agent", {
            "workflow_id": workflow.workflow_id,
            "decision_id": (updated or {}).get("decision_id"),
            "trigger": trigger,
            "candidate_id": cid,
            "candidate_name": profile.get("name"),
            "candidate_rank": profile.get("rank"),
            "compliance_status": req.get("status"),
            "compliance_score": req.get("score"),
            "reasons": reasons,
            "attempts": attempts,
            "message": pending_reason,
        })

    async def apply_human_review(
        self,
        decision_id: str,
        *,
        action: str,
        reviewer: Optional[str] = None,
        reason: Optional[str] = None,
        comments: Optional[str] = None,
        evidence: Optional[list] = None,
        override_crew_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a pending_review decision with a human verdict (HITL).

        approve  → sign on the AI's proposed candidate.
        override → sign on a human-chosen candidate (override_crew_id).
        reject   → record a rejection; nobody is signed on.

        Mirrors the automated path's crew-pool update + precedent recording, so the
        human verdict enters the Precedent Index for future matches. Raises ValueError
        on a bad action, missing decision, or a decision not awaiting review.
        """
        action = (action or "").lower()
        if action not in ("approve", "reject", "override"):
            raise ValueError(f"Unknown review action: {action!r}")

        decision = await get_decision(decision_id)
        if not decision:
            raise ValueError(f"Decision {decision_id} not found")
        if decision.get("review_status") != "pending_review":
            raise ValueError(f"Decision {decision_id} is not awaiting review")

        workflow_id = decision.get("workflow_id")
        proposal = decision.get("ai_proposal") or {}
        compliance_status = decision.get("compliance_status")
        compliance_score = decision.get("compliance_score")
        chosen_crew: Optional[Dict[str, Any]] = None
        chosen_id: Optional[str] = None

        if action == "reject":
            outcome_status = "rejected"
            decision_source = "human"
            outcome_reasons = [f"Rejected by {reviewer or 'reviewer'}"] + ([reason] if reason else [])
        else:
            # approve → the AI's proposed candidate; override → a human-named candidate.
            target_id = override_crew_id if action == "override" else (
                proposal.get("crew_id") or decision.get("chosen_crew_id")
            )
            if not target_id:
                raise ValueError("No candidate to sign on — provide override_crew_id")
            profile = await get_crew_by_id(target_id, pool="signon") \
                or await get_crew_by_id(target_id, pool="signoff")
            if not profile:
                raise ValueError(f"Candidate {target_id} not found")
            chosen_id = target_id
            chosen_crew = {
                "crew_id": target_id, "name": profile.get("name"), "rank": profile.get("rank"),
                "grade": profile.get("grade"), "port": profile.get("port"),
                "nationality": profile.get("nationality"),
            }
            outcome_status = "signed_on"
            decision_source = "ai_then_human" if action == "override" else "human"
            outcome_reasons = [f"Approved by {reviewer or 'reviewer'}"] + ([reason] if reason else [])
            # An override to a DIFFERENT crew supersedes the AI's compliance figures.
            ai_target = proposal.get("crew_id") or decision.get("chosen_crew_id")
            if action == "override" and target_id != ai_target:
                compliance_status = None
                compliance_score = None

        updated = await decision_trace_service.apply_review(
            decision_id,
            action=action,
            reviewer=reviewer,
            outcome_status=outcome_status,
            decision_source=decision_source,
            chosen_crew=chosen_crew,
            chosen_crew_id=chosen_id,
            compliance_status=compliance_status,
            compliance_score=compliance_score,
            outcome_reasons=outcome_reasons,
            reason=reason,
            comments=comments,
            evidence=evidence,
            broadcast=self._event_callback,
        )
        if not updated:
            raise ValueError(f"Failed to apply review to decision {decision_id}")

        # Crew-pool + downstream events, mirroring the automated sign-on / rejection.
        if outcome_status == "signed_on" and chosen_id:
            row = await update_crew(chosen_id, pool="signoff", status="Onboard")
            if row:
                log.info("review.signed_on", crew_id=chosen_id, action=action, reviewer=reviewer)
                await self._event_callback("crew_signed_on", "Human Reviewer", {
                    "workflow_id": workflow_id,
                    "crew_id": chosen_id,
                    "crew_name": (chosen_crew or {}).get("name"),
                    "crew_rank": (chosen_crew or {}).get("rank"),
                    "compliance_status": compliance_status,
                    "compliance_score": compliance_score,
                    "decision_source": decision_source,
                    "reviewer": reviewer,
                    "attempts": updated.get("attempts"),
                    "message": (
                        f"{(chosen_crew or {}).get('name')} signed on by human "
                        f"{'override' if action == 'override' else 'approval'} — added to onboard crew"
                    ),
                })
            else:
                log.warning("review.signon_crew_not_found", crew_id=chosen_id)
        else:
            log.info("review.rejected", decision_id=decision_id, reviewer=reviewer)
            await self._event_callback("sign_on_rejected", "Human Reviewer", {
                "workflow_id": workflow_id,
                "crew_id": decision.get("chosen_crew_id"),
                "crew_name": (decision.get("chosen_crew") or {}).get("name"),
                "compliance_status": compliance_status,
                "compliance_score": compliance_score,
                "decision_source": decision_source,
                "reviewer": reviewer,
                "failures": outcome_reasons,
                "attempts": updated.get("attempts"),
                "message": f"Rejected by human review ({reviewer or 'reviewer'})",
            })

        # Record the human verdict as a precedent so future matches can learn from it.
        await precedent_service.record_placement(updated)

        # Close out the workflow if it's still tracked in memory.
        wf = await state_service.get_workflow(workflow_id) if workflow_id else None
        if wf:
            wf.status = WorkflowStatus.COMPLETED
            await state_service.update_workflow(wf)

        return updated

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

            # Stamp the decision outcome + emit the verdict so the L4 Decision Graph
            # resolves (mirrors the auto path). Without this, a manual sign-on leaves
            # the captured decision stuck on 'pending'. Single-candidate path (no
            # retry loop), so attempts has one entry.
            report = (updated.compliance_result or {}).get("compliance_report") or {}
            subgraph = (updated.compliance_result or {}).get("compliance_subgraph")
            status = report.get("overall_status", "unknown")
            score = report.get("compliance_score")
            warnings = report.get("warnings", []) or []
            failures = report.get("failures", []) or []
            recommendation = report.get("recommendation")
            cid = candidate.get("crew_id")
            attempt = {
                "order": 1, "crew_id": cid, "name": candidate.get("name"),
                "rank": candidate.get("rank"), "compliance_status": status,
                "compliance_score": score, "failures": failures, "warnings": warnings,
            }
            signed = status in ("passed", "warning")

            if signed:
                chosen_crew = {
                    "crew_id": cid, "name": candidate.get("name"), "rank": candidate.get("rank"),
                    "grade": candidate.get("grade"), "port": candidate.get("port"),
                    "nationality": candidate.get("nationality"),
                }
                updated_decision = await decision_trace_service.record_outcome(
                    workflow.workflow_id,
                    outcome_status="signed_on",
                    compliance_status=status, compliance_score=score,
                    outcome_reasons=warnings, attempts=[attempt],
                    chosen_crew=chosen_crew, chosen_crew_id=cid,
                    broadcast=self._event_callback,
                )
                if updated_decision:
                    await precedent_service.record_placement(updated_decision)
                await update_crew(cid, pool="signoff", status="Onboard")
                await self._event_callback("crew_signed_on", "Compliance Agent", {
                    "workflow_id": workflow.workflow_id,
                    "crew_id": cid, "crew_name": candidate.get("name"),
                    "crew_rank": candidate.get("rank"),
                    "compliance_status": status, "compliance_score": score,
                    "warnings": warnings, "recommendation": recommendation,
                    "subgraph": subgraph, "attempts": [attempt],
                    "message": f"{candidate.get('name')} cleared compliance ({status}, {score}%)",
                })
            else:
                updated_decision = await decision_trace_service.record_outcome(
                    workflow.workflow_id,
                    outcome_status="rejected",
                    compliance_status=status, compliance_score=score,
                    outcome_reasons=failures, attempts=[attempt],
                    broadcast=self._event_callback,
                )
                if updated_decision:
                    await precedent_service.record_placement(updated_decision)
                await self._event_callback("sign_on_rejected", "Compliance Agent", {
                    "workflow_id": workflow.workflow_id,
                    "crew_id": cid, "crew_name": candidate.get("name"),
                    "crew_rank": candidate.get("rank"),
                    "compliance_status": status, "compliance_score": score,
                    "failures": failures, "recommendation": recommendation,
                    "subgraph": subgraph, "attempts": [attempt],
                    "message": f"{candidate.get('name')} did not clear compliance ({status}) — not signed on",
                })

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
# end of WorkflowService
