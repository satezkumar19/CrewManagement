// ─── Crew Types ───────────────────────────────────────────────────────────────

export interface CrewMember {
  crew_id: string;
  name: string;
  rank: string;
  grade: string;
  nationality: string;
  vessel: string;
  port: string;
  joining_date?: string;
  medical_expiry?: string;
  passport_expiry?: string;
  stcw_status: string;
  visa_status: string;
  availability?: string;
  experience_years?: number;
  certifications?: string[];
  match_score?: number;
  match_reason?: string;
  status: string;
}

// ─── Agent Types ──────────────────────────────────────────────────────────────

export type AgentStatus =
  | "idle"
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "failed";

export interface ToolCall {
  tool_name: string;
  input: Record<string, unknown>;
  output?: unknown;
  duration_ms?: number;
  timestamp: string;
}

export interface AgentExecution {
  agent_id: string;
  agent_name: string;
  agent_type: string;
  status: AgentStatus;
  current_task?: string;
  tool_calls: ToolCall[];
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
  confidence_score?: number;
  error_message?: string;
  retry_count: number;
  tokens_used: number;
  estimated_cost: number;
  start_time?: string;
  end_time?: string;
  duration_ms?: number;
}

// ─── Workflow Types ───────────────────────────────────────────────────────────

export type WorkflowStatus =
  | "pending"
  | "running"
  | "paused"
  | "waiting"
  | "retrying_compliance"
  | "completed"
  | "failed"
  | "cancelled";

export interface RejectedCandidate {
  attempt: number;
  candidate: Partial<CrewMember>;
  compliance_score?: number;
  reason: string;
  failures?: string[];
}

export interface TimelineEntry {
  timestamp: string;
  event: string;
  agent: string;
}

export interface WorkflowState {
  workflow_id: string;
  status: WorkflowStatus;
  trigger: string;
  sign_off_crew_id?: string;
  sign_off_crew?: CrewMember;
  matched_crew_id?: string;
  matched_crew?: Partial<CrewMember> & {
    confidence_score?: number;
    match_reasons?: string[];
  };
  agent_executions: AgentExecution[];
  timeline: TimelineEntry[];
  memory: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  crew_match_result?: CrewMatchResult;
  travel_result?: TravelResult;
  notification_result?: NotificationResult;
  compliance_result?: ComplianceResult;
  compliance_retries?: number;
  max_compliance_retries?: number;
  rejected_candidates?: RejectedCandidate[];
  total_tokens: number;
  total_cost: number;
  total_duration_ms: number;
}

// ─── Agent Result Types ───────────────────────────────────────────────────────

export interface CrewMatchResult {
  top_match: {
    crew_id: string;
    name: string;
    rank: string;
    grade: string;
    port: string;
    nationality: string;
    confidence_score: number;
    match_reasons: string[];
  };
  ranked_candidates: Array<{
    crew_id: string;
    name: string;
    rank: string;
    confidence_score: number;
    match_reasons: string[];
  }>;
  confidence_score: number;
  summary: string;
}

export interface TravelResult {
  ticket?: {
    booking_ref: string;
    passenger: string;
    departure_port: string;
    destination: string;
    airline: string;
    flight_number: string;
    departure_date: string;
    arrival_date: string;
    departure_time: string;
    arrival_time: string;
    seat_class: string;
    ticket_price_usd: number;
    status: string;
  };
  port_clearance?: {
    clearance_id: string;
    crew_member: string;
    rank: string;
    vessel: string;
    port: string;
    clearance_date: string;
    valid_until: string;
    authority: string;
    status: string;
    remarks: string;
  };
  travel_summary?: {
    summary_id: string;
    crew_name: string;
    documents: string[];
    package_status: string;
  };
  narrative: string;
}

export interface NotificationResult {
  notifications_sent: Array<{
    notification_id: string;
    to: string;
    subject: string;
    body: string;
    priority: string;
    sent_at: string;
    status: string;
    channel: string;
  }>;
  total_count: number;
  narrative: string;
}

export interface ComplianceCheck {
  doc: string;
  status: "PASSED" | "WARNING" | "FAILED";
  detail: string;
  days_remaining?: number;
  missing?: string[];
}

export interface ComplianceReport {
  crew_id: string;
  crew_name: string;
  overall_status: "passed" | "warning" | "failed";
  compliance_score: number;
  document_checks: ComplianceCheck[];
  port_check: {
    port: string;
    issues: string[];
    clearances: string[];
    port_cleared: boolean;
  };
  warnings: string[];
  failures: string[];
  passed_checks: number;
  warning_checks: number;
  failed_checks: number;
  recommendation: string;
}

export interface ComplianceResult {
  compliance_report?: ComplianceReport;
  narrative: string;
}

// ─── Monitoring Types ─────────────────────────────────────────────────────────

export interface SystemMetrics {
  total_workflows: number;
  completed_workflows: number;
  failed_workflows: number;
  active_workflows: number;
  success_rate: number;
  total_tokens: number;
  total_cost: number;
  avg_workflow_duration_ms: number;
  active_websocket_connections: number;
  agent_metrics: Record<string, AgentMetrics>;
}

export interface AgentMetrics {
  total_runs: number;
  completed: number;
  failed: number;
  total_tokens: number;
  total_cost: number;
  avg_duration_ms: number;
}

export interface ROIMetrics {
  time_saved_per_operation_hours: number;
  total_operations: number;
  total_time_saved_hours: number;
  cost_per_operation_usd: number;
  manual_cost_estimate_usd: number;
  automation_savings_usd_per_op: number;
  crew_match_accuracy_percent: number;
  compliance_accuracy_percent: number;
  task_success_rate_percent: number;
  total_tokens_consumed: number;
  total_ai_cost_usd: number;
  agent_metrics: Record<string, AgentMetrics>;
}

// ─── WebSocket Event ──────────────────────────────────────────────────────────

export interface WSEvent {
  event_type: string;
  agent_name?: string;
  workflow_id?: string;
  data: Record<string, unknown>;
  timestamp: string;
}
