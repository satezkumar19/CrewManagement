"use client";
/**
 * OrgMapGraph — renders the OrgMap ownership hierarchy (GET /graph/orgmap/structure)
 * as a React-Flow diagram laid out left→right in columns: Company → Fleet → Vessel.
 * Vessel nodes are the EntityMap nodes OrgMap overlays onto (shared, not duplicated).
 */
import { useMemo } from "react";
import ReactFlow, {
  Background, Controls, Handle, Position, MarkerType,
  type Node, type Edge, type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import type { OrgMapStructureEdge } from "@/lib/api";

export const ORG_TYPE_COLOR: Record<string, string> = {
  Company: "#a855f7",  // purple
  Fleet: "#2f81f7",    // blue
  Vessel: "#10b981",   // green (matches EntityMap Vessel)
  Rank: "#94a3b8",     // slate (the role layer — shown when a vessel is selected)
};

const COLUMN: Record<string, number> = { Company: 0, Fleet: 1, Vessel: 2, Rank: 3 };
const COL_W = 260;
const ROW_H = 84;

// A graph node — the API structure nodes plus the role/Rank nodes the view appends
// client-side when a vessel is selected (with an optional sublabel + short/over flag).
export interface OrgGraphNode {
  id: string;
  type: "Company" | "Fleet" | "Vessel" | "Rank";
  label: string;
  sublabel?: string;   // e.g. "1/1" (have/required) on Rank nodes
  short?: boolean;     // role under-manned (gap > 0) — tints the sublabel red
}

interface OrgNodeData {
  label: string;
  ntype: string;
  sublabel?: string;
  short?: boolean;
  selected?: boolean;
}

function OrgNode({ data }: NodeProps<OrgNodeData>) {
  const accent = ORG_TYPE_COLOR[data.ntype] || "#94a3b8";
  return (
    <div
      style={{
        background: data.selected ? "rgba(20,48,92,0.98)" : "rgba(13,31,60,0.95)",
        border: `${data.selected ? 2.5 : 1.5}px solid ${accent}${data.selected ? "" : "66"}`,
        borderLeft: `5px solid ${accent}`,
        borderRadius: 10,
        padding: "7px 12px",
        minWidth: 140,
        maxWidth: 190,
        boxShadow: data.selected ? `0 0 0 3px ${accent}44, 0 0 14px ${accent}55` : "none",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.6, textTransform: "uppercase", color: accent }}>
        {data.ntype}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</span>
        {data.sublabel && (
          <span style={{ fontSize: 11, fontWeight: 700, color: data.short ? "#f87171" : "#34d399" }}>
            {data.sublabel}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { orgNode: OrgNode };

function layout(nodes: OrgGraphNode[]): Record<string, { x: number; y: number }> {
  const perCol: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const col = COLUMN[n.type] ?? 3;
    const row = perCol[col] ?? 0;
    perCol[col] = row + 1;
    pos[n.id] = { x: col * COL_W, y: row * ROW_H };
  }
  return pos;
}

export default function OrgMapGraph({
  nodes: rawNodes,
  edges: rawEdges,
  height = 560,
  selectedId,
  onNodeClick,
}: {
  nodes: OrgGraphNode[];
  edges: OrgMapStructureEdge[];
  height?: number;
  selectedId?: string | null;
  onNodeClick?: (id: string) => void;
}) {
  const nodes: Node[] = useMemo(() => {
    const pos = layout(rawNodes);
    return rawNodes.map((n) => ({
      id: n.id,
      type: "orgNode",
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        label: n.label, ntype: n.type, sublabel: n.sublabel, short: n.short,
        selected: n.id === selectedId,
      },
      draggable: true,
    }));
  }, [rawNodes, selectedId]);

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
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
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
