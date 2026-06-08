"use client";
/**
 * DecisionGraph (L4) — renders ONE captured placement decision as a graph that
 * REVEALS STEP BY STEP rather than all at once.
 *
 * Two shapes, chosen by the data:
 *
 *  • DEFAULT (single-shot): Query → Decision → Chosen → Alternatives → Outcome.
 *
 *  • RETRY (L4 #4, when a sign-off went through ≥2 compliance attempts): the
 *    middle becomes the attempt CHAIN so the reject→retry→accept journey is the
 *    story — Query → Decision → Attempt 1 (rejected) → Attempt 2 (accepted) →
 *    Outcome. A failed attempt is red; the candidate that cleared is green; if all
 *    failed, the chain ends in a red rejection.
 *
 * Each stage fades in on a timer when a decision is selected, so the viewer follows
 * the flow one step to the next. When the final (outcome) stage appears,
 * `onOutcomeRevealed` fires so the parent can reveal the matching left-card label.
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

type NodeKind = "query" | "decision" | "chosen" | "alt" | "outcome" | "attempt";

const STAGE_MS = 850;         // pause between each reveal step

const KIND_ACCENT: Record<NodeKind, string> = {
  query: "#a78bfa",     // violet — the question
  decision: "#00d4ff",  // accent — the decision point
  chosen: "#22c55e",    // green — selected candidate
  alt: "#64748b",       // slate — considered but not chosen
  outcome: "#f59e0b",   // amber default (recolored by status)
  attempt: "#64748b",   // slate default (recolored per attempt status)
};

const OUTCOME_COLOR: Record<string, string> = {
  signed_on: "#22c55e",
  rejected: "#ef4444",
  pending: "#f59e0b",
};

// Per-compliance-status color for an attempt node / the final edge.
const STATUS_COLOR: Record<string, string> = {
  passed: "#22c55e",
  warning: "#f59e0b",
  failed: "#ef4444",
};

interface DGNodeData {
  label: string;
  sub?: string;
  tag: string;
  kind: NodeKind;
  ring: string;
  accent?: string;      // overrides KIND_ACCENT (attempt nodes color by status)
  stage: number;
  visible: boolean;
  dim?: boolean;
  glow?: boolean;
}

function DGNode({ data }: NodeProps<DGNodeData>) {
  const accent = data.accent || KIND_ACCENT[data.kind];
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
        boxShadow: data.glow ? `0 0 12px ${data.ring}55` : "none",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: accent, width: 6, height: 6 }} />
      <div style={{ fontSize: 8, letterSpacing: 0.5, textTransform: "uppercase", color: accent }}>
        {data.tag}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, color: "#fff", lineHeight: 1.2 }}>{data.label}</div>
      {data.sub && <div style={{ fontSize: 10, color: "#94a3b8" }}>{data.sub}</div>}
      <Handle type="source" position={Position.Right} style={{ background: accent, width: 6, height: 6 }} />
      {/* Bottom handles carry the L4 #4 retry feedback loop (rejected attempt → back
          to the L3 decision node), kept off the left/right forward flow. */}
      <Handle id="loopOut" type="source" position={Position.Bottom} style={{ background: "#ef4444", width: 6, height: 6 }} />
      <Handle id="loopIn" type="target" position={Position.Bottom} style={{ background: "#ef4444", width: 6, height: 6 }} />
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
  // A retry happened when the sign-off went through ≥2 compliance attempts.
  const attempts = useMemo(() => decision?.attempts ?? [], [decision]);
  const retryMode = attempts.length >= 2;

  // Last stage index varies. Default flow tops out at 4. The retry LOOP reveals two
  // steps per attempt — the candidate is selected by L3 (stage 2+2i), then its
  // verdict either loops back to L3 as feedback or reaches the outcome (stage 3+2i)
  // — so the final outcome lands at 2n+1.
  const maxStage = retryMode ? 2 * attempts.length + 1 : 4;

  // Reveal stage, COUPLED to the decision it belongs to (a stale id counts as 0 so
  // nothing reveals early when the walkthrough advances to the next decision).
  const [reveal, setReveal] = useState<{ id: string | null; stage: number }>({ id: null, stage: 0 });
  const decisionId = decision?.decision_id;

  useEffect(() => {
    if (!decisionId) return;
    setReveal({ id: decisionId, stage: 0 });
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let s = 1; s <= maxStage; s++) {
      timers.push(
        setTimeout(
          () => setReveal((r) => (r.id === decisionId ? { id: decisionId, stage: s } : r)),
          STAGE_MS * s
        )
      );
    }
    return () => timers.forEach(clearTimeout);
  }, [decisionId, maxStage]);

  const stage = reveal.id === decisionId ? reveal.stage : 0;

  useEffect(() => {
    if (decisionId && reveal.id === decisionId && reveal.stage >= maxStage) {
      onOutcomeRevealed?.(decisionId);
    }
  }, [reveal, decisionId, onOutcomeRevealed, maxStage]);

  // Build the full graph (with per-node stage tags) — only when the decision changes.
  const base = useMemo(() => {
    if (!decision) return { nodes: [] as Node<DGNodeData>[], edges: [] as Edge[] };
    return retryMode ? buildRetryGraph(decision) : buildDefaultGraph(decision);
  }, [decision, retryMode]);

  // Apply the current reveal stage: nodes fade in by stage; an edge shows once both
  // its endpoints are visible.
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
          // The label (and its background box) render independently of the path's
          // opacity, so hide it entirely until this edge's stage is revealed —
          // otherwise "selects/considered/outcome" show before the flow reaches them.
          label: on ? ed.label : undefined,
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

  let stageLabels: string[];
  if (retryMode) {
    stageLabels = ["Query", "Decision · L3"];
    attempts.forEach((a, i) => {
      stageLabels.push(`Attempt ${i + 1}`);
      const passed = a.compliance_status === "passed" || a.compliance_status === "warning";
      stageLabels.push(i === attempts.length - 1 || passed ? "Outcome" : "Feedback → L3");
    });
  } else {
    stageLabels = ["Query", "Decision", "Chosen crew", "Alternatives", "Outcome"];
  }

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-ocean-accent" />
          <h3 className="text-sm font-semibold text-white">Decision Graph</h3>
          {retryMode && (
            <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
              {attempts.length} attempts
            </span>
          )}
          <span className="text-[10px] text-ocean-accent/80">
            · {stageLabels[Math.min(stage, maxStage)]}
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
        {retryMode ? (
          <>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.failed }} /> Rejected attempt</span>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.passed }} /> Cleared / signed on</span>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: STATUS_COLOR.warning }} /> Conditional</span>
          </>
        ) : (
          <>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.query }} /> Query</span>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.chosen }} /> Chosen</span>
            <span className="flex items-center gap-1"><i className="w-2 h-2 rounded-full" style={{ background: KIND_ACCENT.alt }} /> Considered</span>
          </>
        )}
        <span className="ml-auto text-gray-600">{decision.total_tokens.toLocaleString()} tokens · ${decision.total_cost.toFixed(4)}</span>
      </div>
    </div>
  );
}

// ── Default single-shot graph (Query → Decision → Chosen → Alternatives → Outcome)
function buildDefaultGraph(decision: DecisionTrace): { nodes: Node<DGNodeData>[]; edges: Edge[] } {
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
      tag: "Decision · L3", kind: "decision", ring: KIND_ACCENT.decision, glow: true, stage: 1, visible: true,
      label: "Placement",
      sub: decision.confidence_score != null ? `${decision.confidence_score}% confidence` : undefined,
    },
  });
  e.push(mkEdge("e-q-d", "query", "decision", "triggers", KIND_ACCENT.query, 1));

  n.push({
    id: "chosen", type: "dgNode", position: { x: 520, y: 40 },
    data: {
      tag: "Chosen", kind: "chosen", ring: KIND_ACCENT.chosen, glow: true, stage: 2, visible: true,
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
      tag: "Outcome", kind: "outcome", ring: outcomeColor, accent: outcomeColor, glow: true, stage: 4, visible: true,
      label: outcomeLabel,
      sub: decision.compliance_status
        ? `Compliance: ${decision.compliance_status}${decision.compliance_score != null ? ` (${decision.compliance_score}%)` : ""}`
        : undefined,
    },
  });
  e.push(mkEdge("e-c-o", "chosen", "outcome", decision.compliance_status || "outcome", outcomeColor, 4, decision.outcome_status === "pending"));

  return { nodes: n, edges: e };
}

// ── Retry graph — an L3-centered LOOP, not a straight line. Candidates branch from
// the Decision (L3) node; a rejected candidate loops a "feedback to L3" edge back to
// it, and L3 then selects the next-ranked candidate. The first to clear connects to
// the Outcome; if all fail, the last connects to a Rejected outcome.
function buildRetryGraph(decision: DecisionTrace): { nodes: Node<DGNodeData>[]; edges: Edge[] } {
  const attempts = decision.attempts || [];
  const n = attempts.length;
  const outcomeColor = OUTCOME_COLOR[decision.outcome_status] || OUTCOME_COLOR.pending;
  const dep = decision.query_context?.departing_crew || {};

  const nodes: Node<DGNodeData>[] = [];
  const edges: Edge[] = [];
  const ROW = 130;                 // vertical gap between stacked candidates
  const yc = 30 + (n - 1) * (ROW / 2);  // center the L3 hub against the candidate stack

  nodes.push({
    id: "query", type: "dgNode", position: { x: 0, y: yc },
    data: {
      tag: "Query · Sign-off", kind: "query", ring: KIND_ACCENT.query, stage: 0, visible: true,
      label: dep.name || "Departing crew",
      sub: [dep.rank, dep.port].filter(Boolean).join(" · "),
    },
  });

  // The L3 decision hub — every candidate is selected here, and every rejection
  // feeds back here.
  nodes.push({
    id: "decision", type: "dgNode", position: { x: 250, y: yc },
    data: {
      tag: "Decision · L3", kind: "decision", ring: KIND_ACCENT.decision, glow: true, stage: 1, visible: true,
      label: "Placement",
      sub: decision.confidence_score != null ? `${decision.confidence_score}% confidence` : undefined,
    },
  });
  edges.push(mkEdge("e-q-d", "query", "decision", "triggers", KIND_ACCENT.query, 1));

  // The candidate that clears is always the LAST attempt (the loop breaks on a pass).
  const last = attempts[n - 1] || {};
  const accepted = last.compliance_status === "passed" || last.compliance_status === "warning";
  const connectIdx = n - 1;

  attempts.forEach((a, i) => {
    const status = a.compliance_status || "failed";
    const color = STATUS_COLOR[status] || STATUS_COLOR.failed;
    const passed = status === "passed" || status === "warning";
    const id = `att-${i}`;
    const nodeStage = 2 + 2 * i;     // L3 selects this candidate
    const resStage = 3 + 2 * i;      // its verdict resolves (loop-back or outcome)

    nodes.push({
      id, type: "dgNode", position: { x: 620, y: 30 + i * ROW },
      data: {
        tag: i === 0 ? "Attempt 1 · top match" : `Attempt ${i + 1} · retry`,
        kind: "attempt", ring: color, accent: color, glow: passed, stage: nodeStage, visible: true,
        label: a.name || a.crew_id,
        sub: `${passed ? "Cleared compliance" : "Rejected"}${a.compliance_score != null ? ` · ${a.compliance_score}%` : ""}`,
      },
    });

    // Forward edge: L3 selects this candidate.
    edges.push(mkEdge(
      `e-d-${id}`, "decision", id,
      i === 0 ? "selects #1" : `selects #${i + 1}`,
      KIND_ACCENT.decision, nodeStage, true,
    ));

    // Rejected (and not the final candidate) → feedback loop back to L3.
    if (!passed && i < n - 1) {
      edges.push({
        id: `e-${id}-loop`, source: id, sourceHandle: "loopOut",
        target: "decision", targetHandle: "loopIn",
        label: "rejected → feedback to L3", animated: true, type: "smoothstep",
        data: { stage: resStage },
        style: { stroke: STATUS_COLOR.failed, strokeWidth: 2, strokeDasharray: "5 3" },
        labelStyle: { fill: STATUS_COLOR.failed, fontSize: 9, fontWeight: 600 },
        labelBgStyle: { fill: "#0a1628", fillOpacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color: STATUS_COLOR.failed },
      });
    }
  });

  // Outcome node, connected from the candidate that resolved the loop (the one that
  // cleared, or the last one if all failed).
  const outcomeStage = 3 + 2 * connectIdx;
  const outcomeLabel = decision.outcome_status === "signed_on"
    ? "Signed On"
    : decision.outcome_status === "rejected"
    ? "Rejected"
    : "Pending";
  nodes.push({
    id: "outcome", type: "dgNode", position: { x: 980, y: 30 + connectIdx * ROW },
    data: {
      tag: "Outcome", kind: "outcome", ring: outcomeColor, accent: outcomeColor, glow: true,
      stage: outcomeStage, visible: true,
      label: outcomeLabel,
      sub: decision.compliance_status
        ? `Compliance: ${decision.compliance_status}${decision.compliance_score != null ? ` (${decision.compliance_score}%)` : ""}`
        : undefined,
    },
  });
  edges.push(mkEdge(
    "e-att-o", `att-${connectIdx}`, "outcome",
    accepted ? "signed on" : "rejected", outcomeColor, outcomeStage,
    decision.outcome_status === "pending",
  ));

  return { nodes, edges };
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
