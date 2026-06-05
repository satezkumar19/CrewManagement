"use client";
/**
 * DecisionGraph (L4) — renders ONE captured placement decision as a graph that
 * REVEALS STEP BY STEP rather than all at once:
 *
 *   stage 0  [Departing crew / query]
 *   stage 1   --triggers--> [Decision]
 *   stage 2   --selects--> [Chosen crew]
 *   stage 3   --considered--> [Alternatives] (dimmed)
 *   stage 4   --outcome--> [Outcome]
 *
 * Each stage fades in on a timer when a decision is selected, so the viewer
 * follows the flow one step to the next. When the final (outcome) stage appears,
 * `onOutcomeRevealed` fires so the parent can reveal the matching left-card label
 * only after the graph has shown the outcome.
 *
 * The fixed topology is laid out here (the backend stores the raw data),
 * mirroring the ComplianceGraph pattern.
 */
import { useEffect, useMemo, useState } from "react";
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
import { GitBranch } from "lucide-react";
import type { DecisionTrace } from "@/types";

type NodeKind = "query" | "decision" | "chosen" | "alt" | "outcome";

const MAX_STAGE = 4;          // query → decision → chosen → alternatives → outcome
const STAGE_MS = 850;         // pause between each reveal step

const KIND_ACCENT: Record<NodeKind, string> = {
  query: "#a78bfa",     // violet — the question
  decision: "#00d4ff",  // accent — the decision point
  chosen: "#22c55e",    // green — selected candidate
  alt: "#64748b",       // slate — considered but not chosen
  outcome: "#f59e0b",   // amber default (recolored by status)
};

const OUTCOME_COLOR: Record<string, string> = {
  signed_on: "#22c55e",
  rejected: "#ef4444",
  pending: "#f59e0b",
};

interface DGNodeData {
  label: string;
  sub?: string;
  tag: string;
  kind: NodeKind;
  ring: string;
  stage: number;
  visible: boolean;
  dim?: boolean;
}

function DGNode({ data }: NodeProps<DGNodeData>) {
  const accent = KIND_ACCENT[data.kind];
  const baseOpacity = data.dim ? 0.55 : 1;
  return (
    <div
      style={{
        background: "rgba(13,31,60,0.95)",
        borderTop: `2px solid ${data.ring}`,
        borderRight: `2px solid ${data.ring}`,
        borderBottom: `2px solid ${data.ring}`,
        borderLeft: `5px solid ${accent}`,
        borderRadius: 10,
        padding: "6px 10px",
        minWidth: 130,
        opacity: data.visible ? baseOpacity : 0,
        transform: data.visible ? "none" : "scale(0.9)",
        transition: "opacity 420ms ease, transform 420ms ease",
        pointerEvents: data.visible ? "auto" : "none",
        boxShadow: data.kind === "chosen" || data.kind === "outcome" ? `0 0 12px ${data.ring}55` : "none",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.5, textTransform: "uppercase", color: accent }}>
        {data.tag}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {data.sub && <div style={{ fontSize: 10, color: "#94a3b8" }}>{data.sub}</div>}
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
    </div>
  );
}

const nodeTypes = { dgNode: DGNode };

export default function DecisionGraph({
  decision,
  onOutcomeRevealed,
}: {
  decision: DecisionTrace | null;
  onOutcomeRevealed?: (decisionId: string) => void;
}) {
  // Reveal stage, COUPLED to the decision it belongs to. Coupling the id with
  // the stage is essential: when the walkthrough advances to the next decision,
  // the previous decision's stage is still MAX. If we read a bare `stage`, the
  // "outcome revealed" effect would fire immediately for the new decision (before
  // its flow animates). Tying the stage to an id lets us treat a stale id as 0.
  const [reveal, setReveal] = useState<{ id: string | null; stage: number }>({ id: null, stage: 0 });
  const decisionId = decision?.decision_id;

  useEffect(() => {
    if (!decisionId) return;
    setReveal({ id: decisionId, stage: 0 });
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let s = 1; s <= MAX_STAGE; s++) {
      timers.push(
        setTimeout(
          () => setReveal((r) => (r.id === decisionId ? { id: decisionId, stage: s } : r)),
          STAGE_MS * s
        )
      );
    }
    return () => timers.forEach(clearTimeout);
  }, [decisionId]);

  // Effective stage for the CURRENT decision — a stale id (from the prior
  // decision still mid-transition) counts as 0, so nothing reveals early.
  const stage = reveal.id === decisionId ? reveal.stage : 0;

  // Tell the parent once THIS decision's outcome node has been revealed.
  useEffect(() => {
    if (decisionId && reveal.id === decisionId && reveal.stage >= MAX_STAGE) {
      onOutcomeRevealed?.(decisionId);
    }
  }, [reveal, decisionId, onOutcomeRevealed]);

  // Build the full graph (with per-node stage tags) — only when the decision changes.
  const base = useMemo(() => {
    if (!decision) return { nodes: [] as Node<DGNodeData>[], edges: [] as Edge[] };

    const outcomeColor = OUTCOME_COLOR[decision.outcome_status] || OUTCOME_COLOR.pending;
    const dep = decision.query_context?.departing_crew || {};
    const chosen = decision.chosen_crew || {};
    const alts = decision.alternatives || [];

    const n: Node<DGNodeData>[] = [];
    const e: Edge[] = [];

    n.push({
      id: "query", type: "dgNode", position: { x: 0, y: 130 },
      data: {
        tag: "Query · Sign-off", kind: "query", ring: KIND_ACCENT.query, stage: 0, visible: true,
        label: dep.name || "Departing crew",
        sub: [dep.rank, dep.port].filter(Boolean).join(" · "),
      },
    });

    n.push({
      id: "decision", type: "dgNode", position: { x: 250, y: 130 },
      data: {
        tag: "Decision", kind: "decision", ring: KIND_ACCENT.decision, stage: 1, visible: true,
        label: "Placement",
        sub: decision.confidence_score != null ? `${decision.confidence_score}% confidence` : undefined,
      },
    });
    e.push(mkEdge("e-q-d", "query", "decision", "triggers", KIND_ACCENT.query, 1));

    n.push({
      id: "chosen", type: "dgNode", position: { x: 520, y: 40 },
      data: {
        tag: "Chosen", kind: "chosen", ring: KIND_ACCENT.chosen, stage: 2, visible: true,
        label: chosen.name || "Selected candidate",
        sub: [chosen.rank, chosen.nationality].filter(Boolean).join(" · "),
      },
    });
    e.push(mkEdge("e-d-c", "decision", "chosen", "selects", KIND_ACCENT.chosen, 2, true));

    alts.slice(0, 3).forEach((a, i) => {
      const id = `alt-${i}`;
      n.push({
        id, type: "dgNode", position: { x: 520, y: 150 + i * 78 },
        data: {
          tag: "Considered", kind: "alt", ring: KIND_ACCENT.alt, stage: 3, visible: true, dim: true,
          label: a.name || a.crew_id,
          sub: `${a.confidence_score}%`,
        },
      });
      e.push({
        id: `e-d-${id}`, source: "decision", target: id, label: "considered",
        data: { stage: 3 },
        style: { stroke: KIND_ACCENT.alt, strokeWidth: 1.25, strokeDasharray: "4 3" },
        labelStyle: { fill: KIND_ACCENT.alt, fontSize: 8 },
        labelBgStyle: { fill: "#0a1628", fillOpacity: 0.7 },
        markerEnd: { type: MarkerType.ArrowClosed, color: KIND_ACCENT.alt },
      });
    });

    const outcomeLabel = decision.outcome_status === "signed_on"
      ? "Signed On"
      : decision.outcome_status === "rejected"
      ? "Rejected"
      : "Pending";
    n.push({
      id: "outcome", type: "dgNode", position: { x: 800, y: 40 },
      data: {
        tag: "Outcome", kind: "outcome", ring: outcomeColor, stage: 4, visible: true,
        label: outcomeLabel,
        sub: decision.compliance_status
          ? `Compliance: ${decision.compliance_status}${decision.compliance_score != null ? ` (${decision.compliance_score}%)` : ""}`
          : undefined,
      },
    });
    e.push(mkEdge("e-c-o", "chosen", "outcome", decision.compliance_status || "outcome", outcomeColor, 4, decision.outcome_status === "pending"));

    return { nodes: n, edges: e };
  }, [decision]);

  // Apply the current reveal stage: nodes fade in by stage; an edge shows once
  // both its endpoints are visible.
  const nodes = useMemo<Node<DGNodeData>[]>(
    () => base.nodes.map((nd) => ({ ...nd, data: { ...nd.data, visible: stage >= nd.data.stage } })),
    [base, stage]
  );
  const edges = useMemo<Edge[]>(
    () =>
      base.edges.map((ed) => {
        const edgeStage = (ed.data?.stage as number) ?? 0;
        const on = stage >= edgeStage;
        return {
          ...ed,
          animated: on && !!ed.animated,
          style: { ...ed.style, opacity: on ? 1 : 0, transition: "opacity 420ms ease" },
        };
      }),
    [base, stage]
  );

  if (!decision) {
    return (
      <div className="glass rounded-2xl border border-ocean-border/50 p-10 flex flex-col items-center justify-center text-center" style={{ minHeight: 380 }}>
        <GitBranch className="w-8 h-8 text-ocean-accent/50 mb-3" />
        <p className="text-sm text-gray-400">Select a decision to view its graph</p>
        <p className="text-xs text-gray-600 mt-1">Query → Decision → Chosen crew → Outcome</p>
      </div>
    );
  }

  const stageLabels = ["Query", "Decision", "Chosen crew", "Alternatives", "Outcome"];

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-ocean-accent" />
          <h3 className="text-sm font-semibold text-white">Decision Graph</h3>
          <span className="text-[10px] text-ocean-accent/80">
            · {stageLabels[Math.min(stage, MAX_STAGE)]}
          </span>
        </div>
        <span className="text-[10px] text-gray-500 font-mono">{decision.decision_id.slice(0, 8)}</span>
      </div>

      <div style={{ height: 400 }} className="rounded-xl overflow-hidden border border-ocean-border/40">
        <ReactFlow
          key={decision.decision_id}
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

      <div className="flex items-center gap-3 mt-2 text-[10px] text-gray-400">
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.query }} /> Query</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.chosen }} /> Chosen</span>
        <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.alt }} /> Considered</span>
        <span className="ml-auto text-gray-600">{decision.total_tokens.toLocaleString()} tokens · ${decision.total_cost.toFixed(4)}</span>
      </div>
    </div>
  );
}

function mkEdge(
  id: string, source: string, target: string, label: string, color: string, stage: number, animated = false
): Edge {
  return {
    id, source, target, label, animated,
    data: { stage },
    style: { stroke: color, strokeWidth: 2 },
    labelStyle: { fill: color, fontSize: 9, fontWeight: 600 },
    labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
    markerEnd: { type: MarkerType.ArrowClosed, color },
  };
}
