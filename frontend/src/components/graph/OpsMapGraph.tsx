"use client";
/**
 * OpsMapGraph — renders the OpsMap directly-follows process graph (returned by
 * GET /graph/opsmap/process) as an interactive React-Flow diagram.
 *
 * Activities are laid out left-to-right by their canonical position in the
 * crew-change flow; the three parallel specialists share one column, and the
 * terminal outcomes (Signed On / Sign-On Rejected / Workflow Failed) stack in the
 * last column. Edges carry frequency + average duration; the slowest handoff (the
 * bottleneck) is highlighted in red.
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
import type { OpsMapProcessNode, OpsMapProcessEdge } from "@/lib/api";

// Column per activity — the parallel specialists share column 1; terminals share
// the final column (stacked vertically).
const COLUMN: Record<string, number> = {
  "Sign-Off Initiated": 0,
  "Crew Matching": 1,
  "Travel Arranged": 1,
  "Crew Notified": 1,
  "Sign-Off Confirmed": 2,
  "Compliance Check": 3,
  "Signed On": 4,
  "Sign-On Rejected": 4,
  "Workflow Failed": 4,
};
const COL_W = 230;
const ROW_H = 96;

// Activity accent: terminals carry an outcome colour, everything else is neutral blue.
function activityColor(label: string, terminal: boolean): string {
  if (label === "Signed On") return "#10b981";       // success — green
  if (label === "Sign-On Rejected") return "#f59e0b"; // rejected — amber
  if (label === "Workflow Failed") return "#ef4444";  // failed — red
  return terminal ? "#94a3b8" : "#3b82f6";            // neutral / step — blue
}

interface ActivityNodeData {
  label: string;
  cases: number;
  terminal: boolean;
  selected?: boolean;
  reference?: boolean;   // reference (designed) model — show the actor, not case counts
  actor?: string;
}

function ActivityNode({ data }: NodeProps<ActivityNodeData>) {
  const accent = activityColor(data.label, data.terminal);
  const subtitle = data.reference
    ? (data.actor ?? "")
    : `${data.cases} ${data.cases === 1 ? "case" : "cases"}`;
  return (
    <div
      style={{
        background: data.selected ? "rgba(20,48,92,0.98)" : "rgba(13,31,60,0.95)",
        border: `${data.selected ? 2.5 : 1.5}px solid ${accent}${data.selected ? "" : "88"}`,
        borderTop: `4px solid ${accent}`,
        borderRadius: 10,
        padding: "8px 12px",
        minWidth: 150,
        maxWidth: 190,
        textAlign: "center",
        boxShadow: data.selected ? `0 0 0 3px ${accent}44, 0 0 14px ${accent}55` : "none",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 12, fontWeight: 700, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {subtitle && (
        <div style={{ fontSize: 10, color: accent, marginTop: 2 }}>{subtitle}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
    </div>
  );
}

// Edge colour for the reference model, keyed by the designed transition kind.
const EDGE_KIND_COLOR: Record<string, string> = {
  happy: "#3b6aa0",      // the normal spine — neutral blue
  parallel: "#8b5cf6",   // the concurrent specialist block — violet
  exception: "#f59e0b",  // compliance rejection — amber
  error: "#ef4444",      // workflow failure — red
};

const nodeTypes = { activityNode: ActivityNode };

function layout(nodes: OpsMapProcessNode[]): Record<string, { x: number; y: number }> {
  const perColIndex: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const col = COLUMN[n.label] ?? 5;
    const row = perColIndex[col] ?? 0;
    perColIndex[col] = row + 1;
    pos[n.id] = { x: col * COL_W, y: row * ROW_H };
  }
  return pos;
}

export default function OpsMapGraph({
  nodes: rawNodes,
  edges: rawEdges,
  height = 560,
  bottleneckEdgeId,
  selectedId,
  onNodeClick,
  variant = "discovered",
}: {
  nodes: OpsMapProcessNode[];
  edges: OpsMapProcessEdge[];
  height?: number;
  bottleneckEdgeId?: string | null;
  selectedId?: string | null;
  onNodeClick?: (id: string) => void;
  variant?: "discovered" | "reference";
}) {
  const reference = variant === "reference";
  const nodes: Node[] = useMemo(() => {
    const pos = layout(rawNodes);
    return rawNodes.map((n) => ({
      id: n.id,
      type: "activityNode",
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        label: n.label, cases: n.cases, terminal: n.terminal,
        selected: n.id === selectedId, reference, actor: n.actor,
      },
      draggable: true,
    }));
  }, [rawNodes, selectedId, reference]);

  const edges: Edge[] = useMemo(
    () =>
      rawEdges.map((e) => {
        // Reference model: colour by designed transition kind, dash the parallel block.
        if (reference) {
          const stroke = EDGE_KIND_COLOR[e.kind ?? "happy"] ?? "#3b6aa0";
          const dashed = e.kind === "parallel";
          return {
            id: e.id,
            source: e.source,
            target: e.target,
            label: e.label,
            style: { stroke, strokeWidth: 1.5, strokeDasharray: dashed ? "5 4" : undefined },
            labelStyle: { fill: "#cbd5e1", fontSize: 9, fontWeight: 700 },
            labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
            markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
          };
        }
        // Discovered model: highlight the slowest handoff (bottleneck) in red.
        const isBottleneck = bottleneckEdgeId === e.id;
        const stroke = isBottleneck ? "#ef4444" : "#3b6aa0";
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.label,
          animated: isBottleneck,
          style: { stroke, strokeWidth: isBottleneck ? 2.5 : 1.5 },
          labelStyle: { fill: isBottleneck ? "#fca5a5" : "#94a3b8", fontSize: 9, fontWeight: 600 },
          labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        };
      }),
    [rawEdges, bottleneckEdgeId, reference]
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
