"use client";
/**
 * ComplianceGraph — renders the compliance CONTEXT GRAPH the Compliance Agent
 * reasoned over: the incoming seafarer linked to their nationality, vessel,
 * boarding port and certificates, with blocking nodes/edges highlighted in red
 * and warnings in amber. Fed by signOnOutcome.subgraph (streamed from the backend
 * on crew_signed_on / sign_on_rejected). This is the "visible, demoable" face of
 * the context graph — the same structure the agent uses to decide is shown to the
 * user, instead of the graph being an invisible backend detail.
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
import { Share2 } from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import type { GraphStatus } from "@/types";

const STATUS_COLOR: Record<GraphStatus, string> = {
  ok: "#22c55e",
  warn: "#f59e0b",
  block: "#ef4444",
};

const TYPE_ACCENT: Record<string, string> = {
  Seafarer: "#00d4ff",
  Country: "#a78bfa",
  Vessel: "#38bdf8",
  Port: "#f472b6",
  Certificate: "#94a3b8",
};

interface GraphNodeData {
  label: string;
  sub?: string;
  ntype: string;
  status: GraphStatus;
}

function GraphNode({ data }: NodeProps<GraphNodeData>) {
  const ring = STATUS_COLOR[data.status];
  const accent = TYPE_ACCENT[data.ntype] || "#94a3b8";
  return (
    <div
      style={{
        background: "rgba(13,31,60,0.95)",
        borderTop: `2px solid ${ring}`,
        borderRight: `2px solid ${ring}`,
        borderBottom: `2px solid ${ring}`,
        borderLeft: `5px solid ${accent}`,
        borderRadius: 10,
        padding: "6px 10px",
        minWidth: 120,
        boxShadow: data.status === "block" ? `0 0 12px ${ring}66` : "none",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.5, textTransform: "uppercase", color: accent }}>
        {data.ntype}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {data.sub && <div style={{ fontSize: 10, color: "#94a3b8" }}>{data.sub}</div>}
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { graphNode: GraphNode };

const VERDICT_THEME: Record<string, { label: string; color: string }> = {
  passed: { label: "PASSED", color: "#22c55e" },
  warning: { label: "WARNING", color: "#f59e0b" },
  failed: { label: "FAILED", color: "#ef4444" },
};

export default function ComplianceGraph() {
  const outcome = useWorkflowStore((s) => s.signOnOutcome);
  const sg = outcome?.subgraph;

  const nodes: Node[] = useMemo(
    () =>
      (sg?.nodes || []).map((n) => ({
        id: n.id,
        type: "graphNode",
        position: { x: n.x, y: n.y },
        data: { label: n.label, sub: n.sub, ntype: n.type, status: n.status },
        draggable: true,
      })),
    [sg]
  );

  const edges: Edge[] = useMemo(
    () =>
      (sg?.edges || []).map((e) => {
        const color = STATUS_COLOR[e.status];
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.label,
          animated: e.status === "block",
          style: { stroke: color, strokeWidth: e.status === "ok" ? 1.5 : 2.5 },
          labelStyle: { fill: color, fontSize: 9, fontWeight: 600 },
          labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
          markerEnd: { type: MarkerType.ArrowClosed, color },
        };
      }),
    [sg]
  );

  if (!sg) return null;
  const theme = VERDICT_THEME[sg.verdict] || VERDICT_THEME.warning;

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Share2 className="w-4 h-4 text-ocean-accent" />
          <h3 className="text-sm font-semibold text-white">Compliance Context Graph</h3>
        </div>
        <span
          className="px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider"
          style={{ color: theme.color, border: `1px solid ${theme.color}55`, background: `${theme.color}14` }}
        >
          {theme.label}
        </span>
      </div>

      <div style={{ height: 340 }} className="rounded-xl overflow-hidden border border-ocean-border/40">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
        >
          <Background color="#1e3a5f" gap={18} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 mt-2 text-[10px] text-gray-400">
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.ok }} /> OK</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.warn }} /> Warning</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.block }} /> Blocking</span>
        <span className="ml-auto text-gray-600">backend: {sg.backend}</span>
      </div>

      {/* Graph-derived findings — the plain-language traversal trace */}
      {sg.findings?.length > 0 && (
        <div className="mt-3 pt-3 border-t border-ocean-border/30">
          <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Graph findings</p>
          <ul className="space-y-0.5">
            {sg.findings.map((f, i) => (
              <li key={i} className="text-xs text-gray-300 flex items-start gap-1.5">
                <span className="mt-1 w-1 h-1 rounded-full shrink-0 bg-ocean-accent" />
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
