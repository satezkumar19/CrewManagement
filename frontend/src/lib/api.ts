import axios from "axios";
import type { CrewMember, WorkflowState, SystemMetrics, ROIMetrics, DecisionTrace, PatternReport, SimilarCrewResponse } from "@/types";

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

export const graphApi = {
  getSummary: () => api.get<GraphSummary>("/graph/summary").then(r => r.data),
  getFacets: () => api.get<GraphFacets>("/graph/facets").then(r => r.data),
  getSubgraph: (params: { rank?: string; certificate?: string; port?: string; limit?: number }) =>
    api.get<GraphSubgraph>("/graph/subgraph", { params }).then(r => r.data),
  getNode: (nodeId: string) =>
    api.get<GraphNodeDetail>(`/graph/node/${encodeURIComponent(nodeId)}`).then(r => r.data),
};

// ── Decisions (L4 Decision Graph) ──────────────────────────────────────────────
export const decisionApi = {
  list: (limit?: number) =>
    api.get<DecisionTrace[]>("/decisions/", { params: { limit } }).then(r => r.data),

  get: (id: string) =>
    api.get<DecisionTrace>(`/decisions/${id}`).then(r => r.data),

  seedDemo: () =>
    api.post<{ seeded: number; decisions: DecisionTrace[] }>("/decisions/demo-seed").then(r => r.data),
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
