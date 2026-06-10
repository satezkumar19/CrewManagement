import axios from "axios";
import type { CrewMember, WorkflowState, SystemMetrics, ROIMetrics, DecisionTrace, PatternReport, SimilarCrewResponse, IntelResult } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
  timeout: 30000,
});

// ── Crew ──────────────────────────────────────────────────────────────────────
export const crewApi = {
  getSignOnCrew: () => api.get<CrewMember[]>("/crew/sign-on").then(r => r.data),
  getSignOffCrew: () => api.get<CrewMember[]>("/crew/sign-off").then(r => r.data),
  getCrewMember: (id: string) => api.get<CrewMember>(`/crew/${id}`).then(r => r.data),
};

// ── Workflow ──────────────────────────────────────────────────────────────────
export const workflowApi = {
  initiateSignOff: (crew_id: string, reason?: string) =>
    api.post<{ workflow_id: string; status: string; message: string }>("/workflow/sign-off", {
      crew_id,
      reason,
    }).then(r => r.data),

  initiateSignOn: (workflow_id: string, candidate_crew_id: string) =>
    api.post<{ workflow_id: string; status: string; message: string }>("/workflow/sign-on", {
      workflow_id,
      candidate_crew_id,
    }).then(r => r.data),

  getWorkflow: (id: string) =>
    api.get<WorkflowState>(`/workflow/${id}`).then(r => r.data),

  listWorkflows: (limit?: number) =>
    api.get<WorkflowState[]>("/workflow/", { params: { limit } }).then(r => r.data),

  controlWorkflow: (id: string, action: "pause" | "resume" | "cancel") =>
    api.post(`/workflow/${id}/control`, { action }).then(r => r.data),
};

// ── L3 Intelligence Graph ─────────────────────────────────────────────────────
// The L3 match can run the deterministic backend (~ms) OR the Managed-Agents backend
// (LLM coordinator + 3 sub-agents, which can take 1-2 minutes), so these two calls get
// a generous timeout that overrides the 30s default.
const INTEL_MATCH_TIMEOUT_MS = 180_000;

export const intelligenceApi = {
  // Top-N replacements for a departing crew member (by crew_id).
  match: (crew_id: string, top_n = 3, contract_period_months = 6) =>
    api.post<IntelResult>("/intelligence/match", {
      crew_id, top_n, contract_period_months,
    }, { timeout: INTEL_MATCH_TIMEOUT_MS }).then(r => r.data),

  // Top-N replacements for an explicit vacancy (rank + port), no crew lookup.
  matchContext: (vacated_rank: string, port?: string, top_n = 3, vacated_grade?: string) =>
    api.post<IntelResult>("/intelligence/match-context", {
      vacated_rank, port, top_n, vacated_grade,
    }, { timeout: INTEL_MATCH_TIMEOUT_MS }).then(r => r.data),

  // Sign on the selected (rank-1) replacement candidate: moves them to the onboard
  // pool and records the L3 match score/reason. Returns the sign-on confirmation.
  signOn: (
    crew_id: string,
    score?: number,
    reason?: string,
    vessel?: string,
    workflow_id?: string,
  ) =>
    api.post<{ status: string; pool: string; crew_id: string; name?: string; rank?: string }>(
      "/intelligence/sign-on",
      { crew_id, score, reason, vessel, workflow_id },
    ).then(r => r.data),
};

// ── Monitoring ────────────────────────────────────────────────────────────────
export const monitoringApi = {
  getMetrics: () =>
    api.get<SystemMetrics>("/monitoring/metrics").then(r => r.data),

  getROI: () =>
    api.get<ROIMetrics>("/monitoring/roi").then(r => r.data),

  getActiveWorkflows: () =>
    api.get("/monitoring/workflows/active").then(r => r.data),

  getAgentStatus: () =>
    api.get("/monitoring/agents/status").then(r => r.data),

  getAgentSkills: () =>
    api.get<{ agents: AgentSkills[] }>("/monitoring/agents/skills").then(r => r.data.agents),
};

// ── L2 Knowledge Graph (EntityMap) ──────────────────────────────────────────────
export interface GraphNodeDTO {
  id: string;
  type: string;        // Crew | Vessel | Port | Certificate | Contract
  label: string;
  sub?: string;
  pool?: string;
}
export interface GraphEdgeDTO {
  id: string;
  source: string;
  target: string;
  label: string;       // HOLDS | ASSIGNED_TO | CURRENTLY_AT | CALLS_AT | SIGNED | FOR_VESSEL | AT_PORT
}
export interface GraphSubgraph {
  filters: { rank: string | null; certificate: string | null; port: string | null };
  crew_count: number;
  nodes: GraphNodeDTO[];
  edges: GraphEdgeDTO[];
  total_nodes: number;
  total_edges: number;
  elapsed_ms: number;
}
export interface GraphSummary {
  graph: string;
  dimension: string;
  labels: string[];
  edge_types: string[];
  nodes: Record<string, number>;
  edges: Record<string, number>;
  total_nodes: number;
  total_edges: number;
}
export interface GraphFacets {
  ranks: string[];
  certificates: string[];
  ports: string[];
}
export interface GraphNodeRelationship {
  dir: "in" | "out";
  rel: string;
  other: string;
  other_type: string;
  other_id: number | string;
}
export interface GraphNodeDetail {
  id: string;
  label: string;                       // entity type (Crew | Vessel | ...)
  properties: Record<string, string | number | null>;
  relationships: GraphNodeRelationship[];
  degree: number;
}

// A focused subgraph for one OpsMap process case. `active_ids` are the focal nodes
// (the sign-off crew, vessel and replacement candidate) the UI highlights.
export interface CaseSubgraph {
  case: { crew: string | null; vessel: string | null; candidate: string | null };
  crew_count: number;
  nodes: GraphNodeDTO[];
  edges: GraphEdgeDTO[];
  active_ids: string[];
  total_nodes: number;
  total_edges: number;
  elapsed_ms: number;
}

// One connected graph for a process case across all three L2 dimensions. Each node is
// tagged with a `zone` (process | entity | org) and a `col` for the three-band layout;
// `active` marks the process spine + bridge nodes (the live line).
export interface TraversalNode {
  id: string;
  type: string;       // Activity | Crew | Vessel | Port | Certificate | Contract | Fleet | Company | Rank
  label: string;
  zone: "process" | "entity" | "org";
  col: number;
  sub?: string;
  active: boolean;
}
export interface TraversalEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  active: boolean;
}
export interface CaseTraversal {
  case: {
    case_id: string;
    sign_off_crew: string | null;
    sign_off_rank: string | null;
    sign_off_vessel: string | null;
    sign_on_crew: string | null;
    outcome: OpsMapOutcome;
  };
  candidate_reached: boolean;
  zones: string[];
  nodes: TraversalNode[];
  edges: TraversalEdge[];
  active_ids: string[];
  total_nodes: number;
  total_edges: number;
  elapsed_ms: number;
}

export const graphApi = {
  getSummary: () => api.get<GraphSummary>("/graph/summary").then(r => r.data),
  getFacets: () => api.get<GraphFacets>("/graph/facets").then(r => r.data),
  getSubgraph: (params: { rank?: string; certificate?: string; port?: string; limit?: number }) =>
    api.get<GraphSubgraph>("/graph/subgraph", { params }).then(r => r.data),
  getCaseSubgraph: (params: { crew?: string; vessel?: string; candidate?: string }) =>
    api.get<CaseSubgraph>("/graph/case-subgraph", { params }).then(r => r.data),
  getCaseTraversal: (caseId: string) =>
    api.get<CaseTraversal>("/graph/case-traversal", { params: { case_id: caseId } }).then(r => r.data),
  getNode: (nodeId: string) =>
    api.get<GraphNodeDetail>(`/graph/node/${encodeURIComponent(nodeId)}`).then(r => r.data),
};

// ── L2 Knowledge Graph (OpsMap — process mining) ─────────────────────────────────
export type OpsMapOutcome = "success" | "rejected" | "failed" | "in_progress";

// Edge kind on the reference (designed) model — absent on the mined model.
export type OpsMapEdgeKind = "happy" | "parallel" | "exception" | "error";
export interface OpsMapProcessNode {
  id: string;
  type: "Activity";
  label: string;
  cases: number;
  terminal: boolean;
  actor?: string;      // reference model only — who performs the step
}
export interface OpsMapProcessEdge {
  id: string;
  source: string;
  target: string;
  count: number;
  avg_seconds: number;
  label: string;       // e.g. "3x · 1.3m" (mined) or "pass"/"fail" (reference)
  kind?: OpsMapEdgeKind; // reference model only
}
export interface OpsMapProcess {
  dimension: "OpsMap";
  model?: "reference";   // present on the reference model, absent on the mined one
  nodes: OpsMapProcessNode[];
  edges: OpsMapProcessEdge[];
  metrics: {
    total_cases: number;
    total_activities: number;
    total_transitions: number;
    avg_cycle_time_seconds: number;
    avg_cycle_time_human: string;
  };
}
export interface OpsMapSummary {
  graph: string;
  backend: string;
  dimension: "OpsMap";
  activities: string[];
  total_cases: number;
  total_activities: number;
  total_transitions: number;
  variant_count: number;
  conformance_rate: number;
  avg_cycle_time_human: string;
}
export interface OpsMapVariant {
  id: string;
  path: string[];
  case_count: number;
  percentage: number;
  avg_cycle_time_seconds: number;
  avg_cycle_time_human: string;
  outcome: OpsMapOutcome;
}
export interface OpsMapVariants {
  total_cases: number;
  variant_count: number;
  variants: OpsMapVariant[];
}
export interface OpsMapBottleneck {
  from: string;
  to: string;
  avg_seconds: number;
  avg_human: string;
  occurrences: number;
}
export interface OpsMapBottlenecks {
  bottlenecks: OpsMapBottleneck[];
}
export interface OpsMapDeviation {
  case_id: string;
  path: string[];
  reason: string;
}
export interface OpsMapConformance {
  total_cases: number;
  conformant_cases: number;
  conformance_rate: number;
  happy_path: string[];
  deviations: OpsMapDeviation[];
}

// A single recorded step within a case, carrying the curated record-specific data.
export interface OpsMapCaseStep {
  activity: string;
  actor: string;
  ts_iso: string;
  details: Record<string, string | number | string[]>;
}
export interface OpsMapCase {
  case_id: string;
  sign_off_crew: string | null;
  sign_off_rank: string | null;
  sign_off_vessel: string | null;
  sign_on_crew: string | null;
  outcome: OpsMapOutcome;
  compliance_status: string | null;
  compliance_score: number | null;
  reason: string | null;
  cycle_time_seconds: number;
  cycle_time_human: string;
  started_iso: string | null;
  ended_iso: string | null;
  path: string[];
  steps: OpsMapCaseStep[];
}
export interface OpsMapCases {
  total_cases: number;
  cases: OpsMapCase[];
}

export const opsMapApi = {
  getSummary: () => api.get<OpsMapSummary>("/graph/opsmap/summary").then(r => r.data),
  getProcess: () => api.get<OpsMapProcess>("/graph/opsmap/process").then(r => r.data),
  getReference: () => api.get<OpsMapProcess>("/graph/opsmap/reference").then(r => r.data),
  getVariants: () => api.get<OpsMapVariants>("/graph/opsmap/variants").then(r => r.data),
  getBottlenecks: (limit = 5) =>
    api.get<OpsMapBottlenecks>("/graph/opsmap/bottlenecks", { params: { limit } }).then(r => r.data),
  getConformance: () => api.get<OpsMapConformance>("/graph/opsmap/conformance").then(r => r.data),
  getCases: () => api.get<OpsMapCases>("/graph/opsmap/cases").then(r => r.data),
};

// ── L2 Knowledge Graph (OrgMap — organizational hierarchy) ───────────────────────
export interface OrgMapSummary {
  graph: string;
  dimension: "OrgMap";
  labels: string[];
  edge_types: string[];
  nodes: Record<string, number>;
  edges: Record<string, number>;
  companies: string[];
  fleets: string[];
  total_nodes: number;
  total_edges: number;
}
export interface OrgMapStructureNode {
  id: string;
  type: "Company" | "Fleet" | "Vessel";
  label: string;
}
export interface OrgMapStructureEdge {
  id: string;
  source: string;
  target: string;
  label: string;       // OWNS | OPERATES
}
export interface OrgMapStructure {
  dimension: "OrgMap";
  nodes: OrgMapStructureNode[];
  edges: OrgMapStructureEdge[];
  total_nodes: number;
  total_edges: number;
  elapsed_ms?: number;
}
export interface OrgMapManningRow {
  rank: string;
  required: number;
  have: number;
  gap: number;
  reports_to: string | null;   // the rank this one reports to (null = Master, top of tree)
}
export interface OrgMapManningGap {
  scope: { company: string | null; fleet: string | null; vessel: string | null; vessels: string[] };
  rows: OrgMapManningRow[];
  totals: { required: number; have: number; gap: number };
}

export const orgMapApi = {
  getSummary: () => api.get<OrgMapSummary>("/graph/orgmap/summary").then(r => r.data),
  getStructure: () => api.get<OrgMapStructure>("/graph/orgmap/structure").then(r => r.data),
  getManningGap: (params: { company?: string; fleet?: string; vessel?: string } = {}) =>
    api.get<OrgMapManningGap>("/graph/orgmap/manning-gap", { params }).then(r => r.data),
};

// ── Decisions (L4 Decision Graph) ──────────────────────────────────────────────
export const decisionApi = {
  list: (limit?: number) =>
    api.get<DecisionTrace[]>("/decisions/", { params: { limit } }).then(r => r.data),

  get: (id: string) =>
    api.get<DecisionTrace>(`/decisions/${id}`).then(r => r.data),

  seedDemo: () =>
    api.post<{ seeded: number; already_present: boolean; decisions: DecisionTrace[] }>("/decisions/demo-seed").then(r => r.data),

  // Removes ONLY seeded/sample rows (demo- workflow ids); live data is preserved.
  clearDemo: () =>
    api.delete<{ decisions_removed: number; precedents_removed: number }>("/decisions/demo-seed").then(r => r.data),
};

// ── Pattern Detection (L4 #4) ──────────────────────────────────────────────────
export const patternApi = {
  get: (limit?: number) =>
    api.get<PatternReport>("/patterns/", { params: { limit } }).then(r => r.data),
};

// ── Structural Embeddings (L4 #3) ──────────────────────────────────────────────
export const embeddingApi = {
  similar: (crewId: string, pool?: string, limit?: number) =>
    api.get<SimilarCrewResponse>(`/embeddings/similar/${crewId}`, { params: { pool, limit } }).then(r => r.data),
};

// Capabilities of each managed agent. `tools` are its functions (custom tools
// like sendMail, or the built-in toolset); `skills` are Anthropic document-skill
// packages (pdf/docx/xlsx) — a separate layer.
export interface AgentSkills {
  key: string;
  name: string;
  tools: string[];
  skills: string[];
}

export default api;
