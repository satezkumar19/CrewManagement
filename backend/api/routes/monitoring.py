"""Monitoring and metrics API routes."""
from fastapi import APIRouter
from typing import Dict, Any

from services.state_service import state_service
from api.websockets.workflow_ws import manager
from agents.managed.registry import (
    COORDINATOR_NAME,
    COORDINATOR_SKILLS,
    coordinator_agent_config,
    custom_skill_id_to_name,
    specialist_agent_configs,
)
from agents.skills import list_skill_files

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _tool_labels(tools) -> list:
    """Display label per tool: custom tools expose `name`; built-in toolsets expose `type`."""
    return [t.get("name") or t.get("type") or "tool" for t in (tools or [])]


def _skill_labels(skills, id_to_name) -> list:
    """Readable label per skill: prebuilt skill ids are already friendly (pdf/docx);
    custom skill ids are mapped back to their local name."""
    out = []
    for s in skills or []:
        sid = s.get("skill_id")
        if s.get("type") == "custom":
            out.append(id_to_name.get(sid, sid))
        else:
            out.append(sid)
    return out


@router.get("/agents/skills", response_model=dict)
async def get_agent_skills():
    """Capabilities of each managed agent — both tools and skills.

    Two distinct layers: `tools` are the agent's functions (custom tools like
    sendMail, or the built-in agent toolset); `skills` are skill packages (prebuilt
    pdf/docx/xlsx or custom). Single-sourced from registry.py.
    """
    id_to_name = custom_skill_id_to_name()
    agents = [
        {
            "key": cfg["key"],
            "name": cfg["name"],
            "tools": _tool_labels(cfg.get("tools")),
            # Combine hosted Anthropic skills (pdf/docx/xlsx/custom) with the
            # local markdown role-skill files under agents/skills/<key>/. Both
            # render as tags in the UI's SKILLS section.
            "skills": _skill_labels(cfg.get("skills"), id_to_name)
            + list_skill_files(cfg["key"]),
        }
        for cfg in specialist_agent_configs()
    ]
    coordinator = coordinator_agent_config([])  # roster irrelevant — we only read its tools
    agents.append(
        {
            "key": "coordinator",
            "name": COORDINATOR_NAME,
            "tools": _tool_labels(coordinator.get("tools")),
            "skills": _skill_labels(COORDINATOR_SKILLS, id_to_name),
        }
    )
    return {"agents": agents}


@router.get("/metrics", response_model=dict)
async def get_metrics() -> Dict[str, Any]:
    """Return aggregated system metrics across all workflows."""
    metrics = state_service.get_metrics()
    metrics["active_websocket_connections"] = manager.total_connections
    return metrics


@router.get("/workflows/active", response_model=list)
async def get_active_workflows():
    workflows = await state_service.get_active_workflows()
    return [
        {
            "workflow_id": w.workflow_id,
            "status": w.status,
            "sign_off_crew": w.sign_off_crew.get("name") if w.sign_off_crew else None,
            "created_at": w.created_at.isoformat(),
            "total_tokens": w.total_tokens,
            "total_cost": round(w.total_cost, 6),
            "agent_count": len(w.agent_executions),
        }
        for w in workflows
    ]


@router.get("/agents/status", response_model=dict)
async def get_agent_status():
    """Return current status of all known agents across active workflows."""
    active = await state_service.get_active_workflows()
    agents_status = {}

    all_agent_names = [
        "Master Agent", "Crew Matching Agent",
        "Travel Agent", "Notification Agent", "Compliance Agent"
    ]
    for name in all_agent_names:
        agents_status[name] = {"status": "idle", "current_task": None, "workflow_id": None}

    for wf in active:
        for exec_ in wf.agent_executions:
            name = exec_.agent_name
            if exec_.status.value in ("running", "pending"):
                agents_status[name] = {
                    "status": exec_.status.value,
                    "current_task": exec_.current_task,
                    "workflow_id": wf.workflow_id,
                    "tokens_used": exec_.tokens_used,
                    "duration_ms": exec_.duration_ms,
                }

    return {"agents": agents_status, "timestamp": __import__("datetime").datetime.utcnow().isoformat()}


@router.get("/roi", response_model=dict)
async def get_roi_metrics():
    """Calculate ROI metrics — time saved, cost efficiency, accuracy."""
    metrics = state_service.get_metrics()
    workflows = await state_service.list_workflows(100)
    completed = [w for w in workflows if w.status.value == "completed"]

    # Mock ROI calculations (realistic maritime estimates)
    manual_sign_off_hours = 8  # Hours a manual process takes
    automated_avg_minutes = (
        metrics.get("avg_workflow_duration_ms", 120000) / 60000
    )
    time_saved_hours = max(0, manual_sign_off_hours - automated_avg_minutes / 60)
    time_saved_per_op = round(time_saved_hours, 2)

    compliance_accuracy = 0
    if completed:
        compliance_checks = [
            w.compliance_result.get("compliance_report", {})
            for w in completed
            if w.compliance_result
        ]
        if compliance_checks:
            scores = [c.get("compliance_score", 0) for c in compliance_checks if c]
            compliance_accuracy = round(sum(scores) / len(scores), 1) if scores else 0

    match_accuracies = []
    for w in completed:
        if w.crew_match_result and w.crew_match_result.get("confidence_score"):
            match_accuracies.append(w.crew_match_result["confidence_score"])
    crew_match_accuracy = round(
        sum(match_accuracies) / len(match_accuracies), 1
    ) if match_accuracies else 0

    return {
        "time_saved_per_operation_hours": time_saved_per_op,
        "total_operations": metrics.get("total_workflows", 0),
        "total_time_saved_hours": round(time_saved_per_op * metrics.get("total_workflows", 0), 2),
        "cost_per_operation_usd": round(metrics.get("total_cost", 0) / max(1, metrics.get("total_workflows", 1)), 4),
        "manual_cost_estimate_usd": 250,  # Mock: $250 per manual operation
        "automation_savings_usd_per_op": round(250 - metrics.get("total_cost", 0) / max(1, metrics.get("total_workflows", 1)), 2),
        "crew_match_accuracy_percent": crew_match_accuracy,
        "compliance_accuracy_percent": compliance_accuracy,
        "task_success_rate_percent": metrics.get("success_rate", 0),
        "total_tokens_consumed": metrics.get("total_tokens", 0),
        "total_ai_cost_usd": round(metrics.get("total_cost", 0), 4),
        "agent_metrics": metrics.get("agent_metrics", {}),
    }
