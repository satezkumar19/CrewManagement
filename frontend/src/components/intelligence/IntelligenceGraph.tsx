"use client";
/**
 * IntelligenceGraph — the REAL L3 graph derived from one Supervisor run:
 *
 *   (Vacancy) ─assess→ (Candidate) ─score→ (Dimension) ─L2→ (L2 Fact)
 *
 * Disqualified candidates link to the blocking dimension with the gate reason as the
 * edge label. Fed live by the streamed `intel_graph` event (intel.fitGraph in the
 * store) and reconciled with the final result. Nodes fade/scale in with a staggered
 * delay so the graph "draws itself" as the run lands — the visible face of L3 turning
 * a sign-off into a ranked, explained shortlist over the L2 facts it consulted.
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
import { motion } from "framer-motion";
import { Workflow } from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import type { GraphStatus } from "@/types";

const STATUS_COLOR: Record<GraphStatus, string> = {
  ok: "#22c55e",
  warn: "#f59e0b",
  block: "#ef4444",
};

const TYPE_ACCENT: Record<string, string> = {
  Vacancy: "#00d4ff",
  Candidate: "#38bdf8",
  Dimension: "#a78bfa",
  L2Fact: "#94a3b8",
};

interface IntelNodeData {
  label: string;
  sub?: string | null;
  ntype: string;
  status: GraphStatus;
  index: number; // stagger order for the entrance animation
  gen: number;   // run generation — remounts the node so it re-animates each run
}

function IntelNode({ data }: NodeProps<IntelNodeData>) {
  const ring = STATUS_COLOR[data.status];
  const accent = TYPE_ACCENT[data.ntype] || "#94a3b8";
  return (
    <motion.div
      key={data.gen}
      initial={{ opacity: 0, scale: 0.82 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: data.index * 0.06, type: "spring", stiffness: 260, damping: 22 }}
      style={{
        background: "rgba(13,31,60,0.95)",
        border: `2px solid ${ring}`,
        borderLeft: `5px solid ${accent}`,
        borderRadius: 10,
        padding: "6px 10px",
        minWidth: 116,
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
    </motion.div>
  );
}

const nodeTypes = { intelNode: IntelNode };

export default function IntelligenceGraph() {
  const { intel } = useWorkflowStore();
  const fg = intel.fitGraph;
  const gen = intel.startedAt ?? 0; // changes per run → re-triggers node entrance

  const nodes: Node[] = useMemo(
    () =>
      (fg?.nodes || []).map((n, i) => ({
        id: n.id,
        type: "intelNode",
        position: { x: n.x, y: n.y },
        data: { label: n.label, sub: n.sub, ntype: n.type, status: n.status, index: i, gen },
        draggable: true,
      })),
    [fg, gen]
  );

  const edges: Edge[] = useMemo(
    () =>
      (fg?.edges || []).map((e) => {
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
    [fg]
  );

  if (!fg || fg.nodes.length === 0) return null;

  return (
    <div className="rounded-lg bg-ocean-card/40 border border-ocean-border/40 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Workflow className="w-3.5 h-3.5 text-ocean-accent" />
          <p className="text-[10px] uppercase tracking-wide text-gray-400">Intelligence graph</p>
        </div>
        <span className="text-[10px] text-gray-600">
          {fg.node_count} nodes · {fg.edge_count} edges
        </span>
      </div>

      <div style={{ height: 320 }} className="rounded-xl overflow-hidden border border-ocean-border/40">
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
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.ok }} /> Shortlisted</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.warn }} /> Assessed</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.block }} /> Disqualified</span>
        <span className="ml-auto text-gray-600">L2 backend: {fg.backend}</span>
      </div>
    </div>
  );
}
