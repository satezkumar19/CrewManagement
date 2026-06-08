/**
 * graphApi — the L2 Knowledge Graph client.
 *
 * NOTE: In the live app these declarations live INSIDE `frontend/src/lib/api.ts`
 * (they share the same axios `api` instance as crewApi / workflowApi). This file
 * is an EXTRACTED COPY for the L2 bundle so the module is self-contained. To use
 * it standalone, point `api` at your axios instance (baseURL `${API_URL}/api/v1`).
 */
import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
  timeout: 30000,
});

// ── Types ─────────────────────────────────────────────────────────────────────
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
  other_id: number | string;   // AGE id sent as a STRING (exceeds JS safe-int range)
}
export interface GraphNodeDetail {
  id: string;
  label: string;
  properties: Record<string, string | number | null>;
  relationships: GraphNodeRelationship[];
  degree: number;
}

// ── Client ────────────────────────────────────────────────────────────────────
export const graphApi = {
  getSummary: () => api.get<GraphSummary>("/graph/summary").then(r => r.data),
  getFacets: () => api.get<GraphFacets>("/graph/facets").then(r => r.data),
  getSubgraph: (params: { rank?: string; certificate?: string; port?: string; limit?: number }) =>
    api.get<GraphSubgraph>("/graph/subgraph", { params }).then(r => r.data),
  getNode: (nodeId: string) =>
    api.get<GraphNodeDetail>(`/graph/node/${encodeURIComponent(nodeId)}`).then(r => r.data),
};
