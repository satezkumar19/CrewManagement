"use client";
/**
 * EntityGraph — renders an L2 EntityMap subgraph (returned by GET /graph/subgraph)
 * as an interactive React-Flow diagram. Nodes carry no positions from the backend,
 * so we lay them out left-to-right in columns by entity type:
 *
 *   Crew → Contract → Vessel → Port → Certificate
 *
 * which makes the EntityMap edges (HOLDS, ASSIGNED_TO, CURRENTLY_AT, SIGNED,
 * FOR_VESSEL, AT_PORT, CALLS_AT) all flow in one readable direction.
 */
import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import type { GraphNodeDTO, GraphEdgeDTO } from "@/lib/api";

// Entity-type palette (matches the static render + the L2 docs).
export const TYPE_COLOR: Record<string, string> = {
  Crew: "#3b82f6",
  Contract: "#ef4444",
  Vessel: "#10b981",
  Port: "#f59e0b",
  Certificate: "#a855f7",
};

const COLUMN: Record<string, number> = {
  Crew: 0,
  Contract: 1,
  Vessel: 2,
  Port: 3,
  Certificate: 4,
};
const COL_W = 270;
const ROW_H = 86;

interface EntityNodeData {
  label: string;
  ntype: string;
  sub?: string;
  selected?: boolean;
  active?: boolean;   // on the highlighted OpsMap process path
}

const ACTIVE_ACCENT = "#f59e0b";   // amber — the live process path

function EntityNode({ data }: NodeProps<EntityNodeData>) {
  const accent = TYPE_COLOR[data.ntype] || "#94a3b8";
  const border = data.active ? ACTIVE_ACCENT : accent;
  return (
    <div
      className={data.active ? "process-active" : undefined}
      style={{
        background: data.selected ? "rgba(20,48,92,0.98)" : data.active ? "rgba(50,38,12,0.96)" : "rgba(13,31,60,0.95)",
        border: `${data.active || data.selected ? 2.5 : 1.5}px solid ${data.active ? ACTIVE_ACCENT : `${accent}${data.selected ? "" : "66"}`}`,
        borderLeft: `5px solid ${border}`,
        borderRadius: 10,
        padding: "6px 11px",
        minWidth: 130,
        maxWidth: 180,
        boxShadow: !data.active && data.selected ? `0 0 0 3px ${accent}44, 0 0 14px ${accent}55` : "none",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.6, textTransform: "uppercase", color: accent }}>
        {data.ntype}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {data.sub && data.sub !== data.ntype && (
        <div style={{ fontSize: 10, color: "#94a3b8" }}>{data.sub}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { entityNode: EntityNode };

function layout(nodes: GraphNodeDTO[]): Record<string, { x: number; y: number }> {
  // Stack each type's nodes vertically within its column.
  const perColIndex: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const col = COLUMN[n.type] ?? 5;
    const row = perColIndex[col] ?? 0;
    perColIndex[col] = row + 1;
    pos[n.id] = { x: col * COL_W, y: row * ROW_H };
  }
  return pos;
}

export default function EntityGraph({
  nodes: rawNodes,
  edges: rawEdges,
  height = 520,
  selectedId,
  activeIds,
  onNodeClick,
}: {
  nodes: GraphNodeDTO[];
  edges: GraphEdgeDTO[];
  height?: number;
  selectedId?: string | null;
  activeIds?: string[];           // nodes on the highlighted OpsMap process path
  onNodeClick?: (id: string) => void;
}) {
  const nodes: Node[] = useMemo(() => {
    const pos = layout(rawNodes);
    // Match by digits so a jump id (bare AGE id, e.g. "106…") highlights the
    // rendered node whose id is prefixed (e.g. "n106…").
    const sel = selectedId ? selectedId.replace(/\D/g, "") : null;
    const activeSet = new Set(activeIds ?? []);
    return rawNodes.map((n) => ({
      id: n.id,
      type: "entityNode",
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        label: n.label, ntype: n.type, sub: n.sub,
        selected: !!sel && n.id.replace(/\D/g, "") === sel,
        active: activeSet.has(n.id),
      },
      draggable: true,
    }));
  }, [rawNodes, selectedId, activeIds]);

  const edges: Edge[] = useMemo(
    () =>
      rawEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        style: { stroke: "#3b6aa0", strokeWidth: 1.5 },
        labelStyle: { fill: "#94a3b8", fontSize: 8.5, fontWeight: 600 },
        labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#3b6aa0" },
      })),
    [rawEdges]
  );

  return (
    <div style={{ height }} className="rounded-xl overflow-hidden border border-ocean-border/40 bg-ocean-card/30">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.15}
        maxZoom={1.8}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
      >
        <Background color="#1e3a5f" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
