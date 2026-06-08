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
  | "completed"
  | "failed"
  | "cancelled";

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

// ─── Context Graph (compliance subgraph) ──────────────────────────────────────

export type GraphStatus = "ok" | "warn" | "block";

export interface ComplianceGraphNode {
  id: string;
  type: "Seafarer" | "Country" | "Vessel" | "Port" | "Certificate" | string;
  label: string;
  sub?: string;
  status: GraphStatus;
  x: number;
  y: number;
}

export interface ComplianceGraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  status: GraphStatus;
}

export interface ComplianceSubgraph {
  nodes: ComplianceGraphNode[];
  edges: ComplianceGraphEdge[];
  findings: string[];
  verdict: "passed" | "warning" | "failed";
  backend: string;
  subject?: { crew_id: string; name: string; rank: string; port: string };
}

export interface ComplianceResult {
  compliance_report?: ComplianceReport;
  compliance_subgraph?: ComplianceSubgraph;
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
  // Prompt-cache observability (Step 1). Managed Agents caches the static prompt
  // prefix server-side; cache_hit_rate is reads / (reads + writes) over the
  // cacheable prefix, so it rises as the cache warms across turns.
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cache_hit_rate: number;
  avg_workflow_duration_ms: number;
  active_websocket_connections: number;
  agent_metrics: Record<string, AgentMetrics>;
  // Backend cache observability (Steps 2 & 3). Optional so older backends don't break.
  backend_cache?: BackendCacheMetrics;
}

export interface CacheCounter {
  hits: number;
  misses: number;
  size?: number;
}

export interface BackendCacheMetrics {
  // Step 2 — in-process lru_cache (skills.json + role/skill markdown).
  lru: {
    hits: number;
    misses: number;
    hit_rate: number;
    by_cache: Record<string, CacheCounter>;
  };
  // Step 3 — Redis cache-aside for crew-list queries.
  redis_crew: {
    hits: number;
    misses: number;
    errors: number;
    hit_rate: number;
    available: boolean;
  };
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

// ─── Decision Trace (L4 Decision Graph) ───────────────────────────────────────

export type DecisionOutcomeStatus = "pending" | "signed_on" | "rejected";

export interface DecisionTrajectoryStep {
  kind: "agent" | "tool";
  agent_name: string;
  // agent-step fields
  agent_type?: string;
  status?: string;
  confidence_score?: number;
  tokens_used?: number;
  // tool-step fields
  tool_name?: string;
  input?: string;
  output?: string;
  duration_ms?: number;
  timestamp?: string;
}

export interface DecisionAlternative {
  crew_id: string;
  name: string;
  rank?: string;
  confidence_score: number;
  // L4 #3 — base score before the precedent boost, and the boost applied.
  base_confidence_score?: number;
  precedent_boost?: number;
  match_reasons?: string[];
}

// ─── Precedent Index (L4 #2) ───────────────────────────────────────────────────

export interface PlacementPrecedent {
  precedent_id: string;
  decision_id?: string;
  created_at?: string;
  rank?: string;
  grade?: string;
  port?: string;
  nationality?: string;
  chosen_crew_id?: string;
  chosen_crew_name?: string;
  chosen_crew_rank?: string;
  chosen_crew_nationality?: string;
  chosen_crew_grade?: string;
  confidence_score?: number;
  outcome_status?: string;
  compliance_status?: string;
  compliance_score?: number;
}

export interface PrecedentSummary {
  total: number;
  signed_on: number;
  rejected: number;
  avg_compliance_score?: number | null;
  last_choice?: { name?: string; outcome?: string } | null;
}

export interface PrecedentConsultation {
  is_repeat: boolean;
  matches: PlacementPrecedent[];
  summary: PrecedentSummary;
  query?: { rank?: string; grade?: string; port?: string };
  consulted_at?: string;
}

// ─── Precedent feedback into L3 (L4 #3) ────────────────────────────────────────
// How the consulted precedent re-ranked the matching query. `applied` is false for
// first-time vacancies (no boost) — the UI then renders nothing.
export interface PrecedentFeedbackBoost {
  crew_id: string;
  name?: string;
  nationality?: string;
  boost: number;
}

export interface PrecedentFeedback {
  applied: boolean;
  top_base_score?: number;
  top_adjusted_score?: number;
  lift?: number;
  reranked?: boolean;
  base_winner?: { crew_id: string; name?: string } | null;
  adjusted_winner?: { crew_id: string; name?: string };
  boosted?: PrecedentFeedbackBoost[];
  rationale?: string | null;
}

// ─── Rejection-retry loop (L4 #4) ──────────────────────────────────────────────
// One compliance attempt within a sign-off. The retry loop tries ranked candidates
// in order until one passes (or they're exhausted).
export interface ComplianceAttempt {
  order: number;
  crew_id: string;
  name?: string;
  rank?: string;
  compliance_status?: string;   // passed | warning | failed
  compliance_score?: number | null;
  failures?: string[];
  warnings?: string[];
}

export interface DecisionTrace {
  decision_id: string;
  workflow_id: string;
  created_at?: string;
  resolved_at?: string;
  trigger?: string;
  query_context: {
    departing_crew?: Partial<CrewMember>;
    reason?: string;
  };
  chosen_crew_id?: string;
  chosen_crew: Partial<CrewMember>;
  confidence_score?: number;
  match_reasons: string[];
  alternatives: DecisionAlternative[];
  trajectory: DecisionTrajectoryStep[];
  is_repeat_query?: boolean;
  consulted_precedents?: PrecedentConsultation | null;
  precedent_feedback?: PrecedentFeedback | null;
  outcome_status: DecisionOutcomeStatus;
  compliance_status?: string;
  compliance_score?: number;
  outcome_reasons: string[];
  attempts?: ComplianceAttempt[];
  pending_reason?: string | null;
  session_id?: string;
  total_tokens: number;
  total_cost: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

// ─── Pattern Detection (L4 #4) ─────────────────────────────────────────────────
// Aggregate view over the decision history: which compliance-failure categories
// recur, and the single recurring gap flagged from them.
export interface PatternCategory {
  category: string;
  label: string;
  decisions_affected: number;
  occurrences: number;
  ports: string[];
  ranks: string[];
  examples: string[];
}

export interface RecurringGap extends PatternCategory {
  recommendation: string;
}

export interface PatternReport {
  summary: {
    total: number;
    signed_on: number;
    rejected: number;
    pending: number;
    rejection_rate: number;
  };
  categories: PatternCategory[];
  recurring_gap: RecurringGap | null;
  generated_at: string;
}

// ─── Structural Embeddings (L4 #3) ─────────────────────────────────────────────
export interface SimilarCrew {
  crew_id: string;
  name?: string;
  rank?: string;
  grade?: string;
  nationality?: string;
  vessel?: string;
  port?: string;
  status?: string;
  pool?: string;
  similarity: number;          // cosine similarity in [0, 1]
}

export interface SimilarCrewResponse {
  crew_id: string;
  backend: string;             // "pgvector" | "fallback"
  count: number;
  matches: SimilarCrew[];
}

// ─── WebSocket Event ──────────────────────────────────────────────────────────

export interface WSEvent {
  event_type: string;
  agent_name?: string;
  workflow_id?: string;
  data: Record<string, unknown>;
  timestamp: string;
}
