import axios from "axios";
import type { CrewMember, WorkflowState, SystemMetrics, ROIMetrics, DecisionTrace } from "@/types";

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

// ── Decisions (L4 Decision Graph) ──────────────────────────────────────────────
export const decisionApi = {
  list: (limit?: number) =>
    api.get<DecisionTrace[]>("/decisions/", { params: { limit } }).then(r => r.data),

  get: (id: string) =>
    api.get<DecisionTrace>(`/decisions/${id}`).then(r => r.data),

  seedDemo: () =>
    api.post<{ seeded: number; decisions: DecisionTrace[] }>("/decisions/demo-seed").then(r => r.data),
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
