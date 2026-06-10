"use client";
/**
 * TraversalGraph — ONE connected graph for a single process case across all three L2
 * dimensions. Nodes are laid out in three left→right bands by their backend `col`:
 *
 *   PROCESS (OpsMap)   ENTITIES (EntityMap)        ORG (OrgMap)
 *   the activity path  sign-off crew · vessel ·    vessel → fleet → company (+ rank)
 *   = the active line  replacement · certs/ports
 *
 * The Vessel (col 2) is the shared bridge between the entity and org bands. The process
 * spine + the process→crew bridge edges are flagged `active` and rendered as an amber,
 * animated "live line". Node colour comes from each dimension's own palette so the three
 * kinds of map node stay visually distinct.
 */
import { useMemo } from "react";
import ReactFlow, {
  Background, Controls, Handle, Position, MarkerType,
  type Node, type Edge, type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import type { TraversalNode, TraversalEdge } from "@/lib/api";
import { TYPE_COLOR } from "./EntityGraph";
import { ORG_TYPE_COLOR } from "./OrgMapGraph";

const PROCESS_COLOR = "#2dd4bf";   // teal — the OpsMap process band
const ACTIVE_ACCENT = "#f59e0b";   // amber — the live process line
const COL_W = 235;
const ROW_H = 74;

function accentFor(n: TraversalNode): string {
  if (n.zone === "process") return PROCESS_COLOR;
  if (n.zone === "org") return ORG_TYPE_COLOR[n.type] || "#94a3b8";
  return TYPE_COLOR[n.type] || "#94a3b8";   // entity
}

interface TNodeData {
  label: string;
  ntype: string;
  sub?: string;
  accent: string;
  active?: boolean;
  isLabel?: boolean;
}

function TNode({ data }: NodeProps<TNodeData>) {
  if (data.isLabel) {
    return (
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase",
        color: data.accent, opacity: 0.85, whiteSpace: "nowrap" }}>
        {data.label}
      </div>
    );
  }
  const border = data.active ? ACTIVE_ACCENT : data.accent;
  return (
    <div
      className={data.active ? "process-active" : undefined}
      style={{
        background: data.active ? "rgba(50,38,12,0.96)" : "rgba(13,31,60,0.95)",
        border: `${data.active ? 2.5 : 1.5}px solid ${data.active ? ACTIVE_ACCENT : `${data.accent}88`}`,
        borderLeft: `5px solid ${border}`,
        borderRadius: 10,
        padding: "6px 11px",
        minWidth: 130,
        maxWidth: 190,
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: data.accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.6, textTransform: "uppercase", color: data.accent }}>
        {data.ntype}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {data.sub && data.sub !== data.ntype && (
        <div style={{ fontSize: 10, color: "#94a3b8" }}>{data.sub}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: data.accent, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { tNode: TNode };

// Stack each column's nodes vertically; remember each column's centre x for zone headers.
function layout(nodes: TraversalNode[]): Record<string, { x: number; y: number }> {
  const perCol: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const row = perCol[n.col] ?? 0;
    perCol[n.col] = row + 1;
    pos[n.id] = { x: n.col * COL_W, y: row * ROW_H };
  }
  return pos;
}

// One header label per zone, centred over that zone's columns.
const ZONES: { zone: TraversalNode["zone"]; label: string; cols: number[]; color: string }[] = [
  { zone: "process", label: "Process", cols: [0], color: PROCESS_COLOR },
  { zone: "entity", label: "Entities", cols: [1, 2, 3], color: TYPE_COLOR.Crew },
  { zone: "org", label: "Org", cols: [4, 5, 6], color: ORG_TYPE_COLOR.Company },
];

export default function TraversalGraph({
  nodes: rawNodes,
  edges: rawEdges,
  height = 600,
  onNodeClick,
}: {
  nodes: TraversalNode[];
  edges: TraversalEdge[];
  height?: number;
  onNodeClick?: (id: string) => void;
}) {
  const nodes: Node[] = useMemo(() => {
    const pos = layout(rawNodes);
    const present = new Set(rawNodes.map((n) => n.col));
    const out: Node[] = rawNodes.map((n) => ({
      id: n.id,
      type: "tNode",
      position: pos[n.id] || { x: 0, y: 0 },
      data: { label: n.label, ntype: n.type, sub: n.sub, accent: accentFor(n), active: n.active },
      draggable: true,
    }));
    // Zone header labels above the columns that actually have nodes.
    for (const z of ZONES) {
      const cols = z.cols.filter((c) => present.has(c));
      if (!cols.length) continue;
      const x = (cols.reduce((a, c) => a + c, 0) / cols.length) * COL_W;
      out.push({
        id: `zone:${z.zone}`,
        type: "tNode",
        position: { x, y: -66 },
        data: { label: z.label, ntype: "", accent: z.color, isLabel: true },
        draggable: false,
        selectable: false,
      });
    }
    return out;
  }, [rawNodes]);

  const edges: Edge[] = useMemo(
    () =>
      rawEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label || undefined,
        animated: e.active,
        style: e.active
          ? { stroke: ACTIVE_ACCENT, strokeWidth: 2.5 }
          : { stroke: "#3b6aa0", strokeWidth: 1.5 },
        labelStyle: { fill: e.active ? "#fbbf24" : "#94a3b8", fontSize: 9, fontWeight: 700 },
        labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: e.active ? ACTIVE_ACCENT : "#3b6aa0" },
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
        fitViewOptions={{ padding: 0.16 }}
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
