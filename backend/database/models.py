"""
Pydantic models for all entities in the Maritime Crew Orchestrator.
These are used for API validation and in-memory state (no real DB dependency for demo).
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from datetime import datetime
from enum import Enum
import uuid


# ─── Enums ───────────────────────────────────────────────────────────────────

class AgentStatus(str, Enum):
    IDLE = "idle"
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"  # Phase 1 complete; awaiting the user's "Sign On" confirmation
    RETRYING_COMPLIANCE = "retrying_compliance"  # Last compliance attempt failed; trying next ranked candidate
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ComplianceStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class CrewStatus(str, Enum):
    AVAILABLE = "Available"
    ONBOARD = "Onboard"
    SIGNING_OFF = "Signing Off"
    SIGNED_OFF = "Signed Off"
    SIGNING_ON = "Signing On"
    SIGNED_ON = "Signed On"
    MATCHED = "Matched"


# ─── Crew Models ─────────────────────────────────────────────────────────────

class CrewMember(BaseModel):
    crew_id: str
    name: str
    rank: str
    grade: str
    nationality: str
    vessel: str
    port: str
    joining_date: Optional[str] = None
    medical_expiry: Optional[str] = None
    passport_expiry: Optional[str] = None
    stcw_status: str = "Valid"
    visa_status: str = "Valid"
    availability: Optional[str] = "Available"
    experience_years: Optional[int] = 0
    certifications: Optional[List[str]] = []
    match_score: Optional[float] = None
    match_reason: Optional[str] = None
    status: str = "Available"


# ─── Agent Models ─────────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    tool_name: str
    input: Dict[str, Any]
    output: Optional[Any] = None
    duration_ms: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentExecution(BaseModel):
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str
    agent_type: str
    status: AgentStatus = AgentStatus.PENDING
    current_task: Optional[str] = None
    tool_calls: List[ToolCall] = []
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    confidence_score: Optional[float] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    tokens_used: int = 0
    estimated_cost: float = 0.0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_ms: Optional[int] = None


# ─── Workflow Models ──────────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    step_name: str
    agent_name: str
    status: AgentStatus = AgentStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None


class WorkflowState(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: WorkflowStatus = WorkflowStatus.PENDING
    trigger: str = ""
    # Managed Agents coordinator session backing this workflow. Created in Phase 1
    # (sign-off) and reused in Phase 2 (sign-on/compliance) so the coordinator
    # retains context across the human-in-the-loop pause.
    session_id: Optional[str] = None
    sign_off_crew_id: Optional[str] = None
    sign_off_crew: Optional[Dict[str, Any]] = None
    matched_crew_id: Optional[str] = None
    matched_crew: Optional[Dict[str, Any]] = None
    steps: List[WorkflowStep] = []
    agent_executions: List[AgentExecution] = []
    timeline: List[Dict[str, Any]] = []
    memory: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Results from agents
    crew_match_result: Optional[Dict[str, Any]] = None
    travel_result: Optional[Dict[str, Any]] = None
    notification_result: Optional[Dict[str, Any]] = None
    compliance_result: Optional[Dict[str, Any]] = None

    # Compliance retry loop — see workflow_service._run_compliance_orchestration.
    # When a candidate's compliance fails, the service iterates down the ranked
    # list (up to max_compliance_retries) instead of failing the workflow.
    compliance_retries: int = 0
    max_compliance_retries: int = 3
    rejected_candidates: List[Dict[str, Any]] = []

    # Metrics
    total_tokens: int = 0
    total_cost: float = 0.0
    total_duration_ms: int = 0


# ─── Request / Response Models ────────────────────────────────────────────────

class InitiateSignOffRequest(BaseModel):
    crew_id: str
    reason: Optional[str] = "Contract completion"


class InitiateSignOnRequest(BaseModel):
    candidate_crew_id: str
    workflow_id: str


class WorkflowControlRequest(BaseModel):
    action: str  # pause | resume | cancel | retry


class NotificationRecord(BaseModel):
    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recipient: str
    subject: str
    body: str
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    channel: str = "email"
    status: str = "sent"


class FlightBooking(BaseModel):
    booking_ref: str
    passenger_name: str
    departure_port: str
    destination: str
    departure_date: str
    arrival_date: str
    airline: str
    flight_number: str
    seat_class: str = "Economy"
    ticket_price: float
    status: str = "Confirmed"


class PortClearance(BaseModel):
    clearance_id: str
    vessel: str
    port: str
    crew_member: str
    rank: str
    clearance_date: str
    valid_until: str
    authority: str
    status: str = "Approved"


class ComplianceReport(BaseModel):
    crew_id: str
    crew_name: str
    overall_status: ComplianceStatus
    compliance_score: float
    checks: List[Dict[str, Any]]
    warnings: List[str] = []
    failures: List[str] = []
    checked_at: datetime = Field(default_factory=datetime.utcnow)


# ─── WebSocket Event ──────────────────────────────────────────────────────────

class WSEvent(BaseModel):
    event_type: str
    workflow_id: Optional[str] = None
    agent_name: Optional[str] = None
    data: Dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)
