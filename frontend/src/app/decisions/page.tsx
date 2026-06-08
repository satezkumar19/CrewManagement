"use client";
/**
 * L4 Decision Graph page.
 *
 * Shows the persisted Decision Traces captured from L3: a selectable list of
 * placement decisions on the left, and for the selected one a Decision Graph
 * (query → decision → chosen → outcome) plus the agent trajectory that produced
 * it. Updates live: when the backend broadcasts `decision_logged` /
 * `decision_outcome`, the list refetches. A "Seed demo data" button populates
 * mock decisions so the view is demoable without running a workflow.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import toast, { Toaster } from "react-hot-toast";
import {
  Anchor, Ship, Activity, BarChart3, GitBranch, Wifi, WifiOff,
  RefreshCw, Sparkles, CheckCircle, XCircle, Clock, ChevronRight, Cpu, Wrench,
  Play, Square, AlertTriangle, RotateCcw, Share2,
} from "lucide-react";

import { decisionApi } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useWorkflowStore } from "@/store/workflowStore";
import DecisionGraph from "@/components/decisions/DecisionGraph";
import PrecedentPanel from "@/components/decisions/PrecedentPanel";
import PatternPanel from "@/components/decisions/PatternPanel";
import SimilarCrewPanel from "@/components/decisions/SimilarCrewPanel";
import type { DecisionTrace, DecisionTrajectoryStep, ComplianceAttempt } from "@/types";

// How long each decision stays on screen during the auto-play walkthrough.
const DEMO_STEP_MS = 5500;

const OUTCOME_BADGE: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  signed_on: { label: "Signed On", color: "#22c55e", icon: <CheckCircle className="w-3.5 h-3.5" /> },
  rejected: { label: "Rejected", color: "#ef4444", icon: <XCircle className="w-3.5 h-3.5" /> },
  pending: { label: "Pending", color: "#f59e0b", icon: <Clock className="w-3.5 h-3.5" /> },
};

export default function DecisionsPage() {
  const { isConnected } = useWebSocket();
  const events = useWorkflowStore((s) => s.events);
  const [decisions, setDecisions] = useState<DecisionTrace[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const lastRefreshKey = useRef<string>("");

  // Auto-play walkthrough: a queue of decision_ids and the current position.
  // demoPos === -1 means idle (no walkthrough running).
  const [demoQueue, setDemoQueue] = useState<string[]>([]);
  const [demoPos, setDemoPos] = useState<number>(-1);

  // Which decisions have had their OUTCOME revealed by the right-side graph.
  // The left card's outcome label is gated on this, so the label only appears
  // after the graph animates through to its outcome node.
  const [revealedOutcomes, setRevealedOutcomes] = useState<Set<string>>(new Set());
  const handleOutcomeRevealed = useCallback((id: string) => {
    setRevealedOutcomes((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  // Mirror demoPos in a ref so the live-event handler can tell whether a seed
  // walkthrough is running WITHOUT a live sign-off ever interrupting it.
  const demoActiveRef = useRef(false);
  useEffect(() => { demoActiveRef.current = demoPos >= 0; }, [demoPos]);

  const load = useCallback(async (): Promise<DecisionTrace[]> => {
    try {
      const list = await decisionApi.list(50);
      setDecisions(list);
      // Intentionally do NOT auto-select a decision — the right panel starts on
      // its empty state and decisions are revealed one by one via the walkthrough
      // (or a manual click).
      return list;
    } catch {
      toast.error("Failed to load decisions", { id: "dec-load" });
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startDemo = useCallback((ids: string[]) => {
    if (ids.length === 0) return;
    // Reset gating so every card starts on "Awaiting outcome" — each label only
    // reappears as the graph animates through to that decision's outcome.
    setRevealedOutcomes(new Set());
    setSelectedId(null);
    setDemoQueue(ids);
    setDemoPos(0);
  }, []);

  const stopDemo = useCallback(() => setDemoPos(-1), []);

  // Walkthrough driver: select the current decision, then advance after a beat.
  // Cleanup clears the timer so manual interaction / stop cancels cleanly.
  useEffect(() => {
    if (demoPos < 0) return;
    if (demoPos >= demoQueue.length) {
      setDemoPos(-1);
      return;
    }
    setSelectedId(demoQueue[demoPos]);
    const t = setTimeout(() => setDemoPos((p) => p + 1), DEMO_STEP_MS);
    return () => clearTimeout(t);
  }, [demoPos, demoQueue]);

  // Live refresh: a decision_logged / decision_outcome event means the table
  // changed. On a freshly LOGGED decision (a real sign-off), auto-select it so
  // its flow animates step-by-step; its outcome node then updates live when the
  // decision_outcome event lands after compliance. A running seed walkthrough is
  // never interrupted (guarded by demoActiveRef).
  useEffect(() => {
    const latest = events[0];
    if (!latest) return;

    // Precedent Index consulted at the start of a sign-off (before the decision
    // is logged) — surface it as a toast so the lookup is visible live.
    if (latest.event_type === "precedent_consulted") {
      const key = `precedent:${latest.timestamp}`;
      if (key !== lastRefreshKey.current) {
        lastRefreshKey.current = key;
        const d = latest.data || {};
        if (!demoActiveRef.current) {
          toast(
            d.is_repeat
              ? `Precedent Index: ${d.count} prior placement(s) for ${d.rank} @ ${d.port}`
              : `Precedent Index: first placement for ${d.rank} @ ${d.port}`,
            { icon: "📚" }
          );
        }
      }
      return;
    }

    if (latest.event_type !== "decision_logged" && latest.event_type !== "decision_outcome") return;

    const key = `${latest.event_type}:${latest.timestamp}`;
    if (key === lastRefreshKey.current) return;
    lastRefreshKey.current = key;

    const newId = latest.data?.decision_id as string | undefined;
    load().then(() => {
      if (latest.event_type === "decision_logged" && !demoActiveRef.current && newId) {
        toast.success("New decision — showing live flow");
        // Re-gate this id so its outcome label waits for the graph to reach the
        // outcome node, then auto-select it to kick off the step-by-step reveal.
        setRevealedOutcomes((prev) => {
          if (!prev.has(newId)) return prev;
          const next = new Set(prev); next.delete(newId); return next;
        });
        setSelectedId(newId);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  const handleSeed = async () => {
    setSeeding(true);
    try {
      const res = await decisionApi.seedDemo();
      toast.success(`Seeded ${res.seeded} decisions — playing walkthrough`);
      const list = await load();
      // Walk the list as displayed: top to bottom.
      startDemo(list.map((d) => d.decision_id));
    } catch {
      toast.error("Failed to seed demo data");
    } finally {
      setSeeding(false);
    }
  };

  // Replay the walkthrough over the decisions as displayed, top to bottom.
  const handlePlay = () => startDemo(decisions.map((d) => d.decision_id));

  const selectCard = (id: string) => {
    stopDemo();         // any manual click takes over from the walkthrough
    setSelectedId(id);
  };

  const selected = decisions.find((d) => d.decision_id === selectedId) || null;

  return (
    <div className="min-h-screen bg-ocean-gradient">
      <Toaster position="top-right" toastOptions={{
        style: { background: "#0d1f3c", color: "#e2e8f0", border: "1px solid #1e3a5f" }
      }} />

      {/* Nav */}
      <nav className="border-b border-ocean-border bg-ocean-card/90 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-screen-2xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-accent-gradient flex items-center justify-center">
              <Anchor className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold gradient-text">MarineCrewOS</h1>
              <p className="text-xs text-gray-500">Autonomous Crew Orchestrator</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <NavLink href="/" icon={<Ship className="w-4 h-4" />} label="Dashboard" />
            <NavLink href="/workflow" icon={<Activity className="w-4 h-4" />} label="Workflow" />
            <NavLink href="/graph" icon={<Share2 className="w-4 h-4" />} label="Graph" />
            <NavLink href="/monitoring" icon={<BarChart3 className="w-4 h-4" />} label="Monitoring" />
            <NavLink href="/decisions" icon={<GitBranch className="w-4 h-4" />} label="Decisions" active />
          </div>
          <div className="flex items-center gap-2 text-xs">
            {isConnected ? (
              <><Wifi className="w-3 h-3 text-green-400" /><span className="text-green-400">Live</span></>
            ) : (
              <><WifiOff className="w-3 h-3 text-red-400" /><span className="text-red-400">Offline</span></>
            )}
          </div>
        </div>
      </nav>

      <div className="max-w-screen-2xl mx-auto px-6 py-6">
        {/* Header row */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <GitBranch className="w-5 h-5 text-ocean-accent" /> Decision Graph
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              Every placement decision L3 makes, captured as a trace: query → decision → chosen crew → outcome.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => load()}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm glass border border-ocean-border/40 text-gray-300 hover:text-white transition"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </button>
            {demoPos >= 0 ? (
              <button
                onClick={stopDemo}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm glass border border-red-500/40 text-red-300 hover:text-white transition"
              >
                <Square className="w-3.5 h-3.5" /> Stop
              </button>
            ) : (
              <button
                onClick={handlePlay}
                disabled={decisions.length === 0}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm glass border border-ocean-border/40 text-gray-300 hover:text-white disabled:opacity-40 transition"
              >
                <Play className="w-3.5 h-3.5" /> Play
              </button>
            )}
            <button
              onClick={handleSeed}
              disabled={seeding}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-sm bg-accent-gradient text-white shadow-lg disabled:opacity-60 transition"
            >
              <Sparkles className={`w-3.5 h-3.5 ${seeding ? "animate-pulse" : ""}`} /> Seed &amp; play
            </button>
          </div>
        </div>

        {/* Walkthrough progress banner — shown while the auto-play is running. */}
        {demoPos >= 0 && demoPos < demoQueue.length && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-5 glass rounded-2xl border border-ocean-accent/40 px-4 py-3"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="flex items-center gap-2 text-sm text-white">
                <span className="w-2 h-2 rounded-full bg-ocean-accent animate-pulse" />
                Demo walkthrough — Decision {demoPos + 1} of {demoQueue.length}
              </span>
              <button onClick={stopDemo} className="flex items-center gap-1 text-xs text-red-300 hover:text-red-200">
                <Square className="w-3 h-3" /> Stop
              </button>
            </div>
            <div className="h-1.5 rounded-full bg-ocean-border/40 overflow-hidden">
              <motion.div
                key={demoPos}
                className="h-full bg-accent-gradient"
                initial={{ width: 0 }}
                animate={{ width: "100%" }}
                transition={{ duration: DEMO_STEP_MS / 1000, ease: "linear" }}
              />
            </div>
          </motion.div>
        )}

        {/* L4 #4 — pattern detection builds up incrementally: it aggregates only the
            decisions whose outcome has been revealed so far (the walkthrough reveals
            them one crew member at a time), so counts/reject-rate/recurring-gap start
            at zero and grow as each outcome lands. */}
        <PatternPanel decisions={decisions.filter((d) => revealedOutcomes.has(d.decision_id))} />

        {/* L4 #3 — Structural Embeddings made visible: pick a crew member, see the
            structurally nearest crew (pgvector / fallback). */}
        <SimilarCrewPanel />

        <div className="grid grid-cols-12 gap-6">
          {/* Left: decision list */}
          <div className="col-span-12 lg:col-span-4 space-y-3">
            {loading ? (
              <div className="glass rounded-2xl p-10 flex flex-col items-center gap-3">
                <div className="w-10 h-10 border-4 border-ocean-accent/30 border-t-ocean-accent rounded-full animate-spin" />
                <p className="text-gray-400 text-sm">Loading decisions…</p>
              </div>
            ) : decisions.length === 0 ? (
              <div className="glass rounded-2xl p-8 text-center">
                <GitBranch className="w-8 h-8 text-ocean-accent/40 mx-auto mb-3" />
                <p className="text-sm text-gray-300">No decisions captured yet</p>
                <p className="text-xs text-gray-500 mt-1">
                  Run a sign-off on the Dashboard, or click <span className="text-ocean-accent">Seed demo data</span> to populate.
                </p>
              </div>
            ) : (
              decisions.map((d) => (
                <DecisionCard
                  key={d.decision_id}
                  decision={d}
                  active={d.decision_id === selectedId}
                  playing={demoPos >= 0 && d.decision_id === demoQueue[demoPos]}
                  outcomeRevealed={revealedOutcomes.has(d.decision_id)}
                  onClick={() => selectCard(d.decision_id)}
                />
              ))
            )}
          </div>

          {/* Right: graph + trajectory */}
          <div className="col-span-12 lg:col-span-8 space-y-4">
            <DecisionGraph decision={selected} onOutcomeRevealed={handleOutcomeRevealed} />
            {selected && <PrecedentPanel decision={selected} />}
            {selected && <TrajectoryTrace decision={selected} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function DecisionCard({ decision, active, playing = false, outcomeRevealed = false, onClick }: { decision: DecisionTrace; active: boolean; playing?: boolean; outcomeRevealed?: boolean; onClick: () => void }) {
  const badge = OUTCOME_BADGE[decision.outcome_status] || OUTCOME_BADGE.pending;
  const dep = decision.query_context?.departing_crew || {};
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ scale: 1.01 }}
      animate={playing ? { scale: [1, 1.015, 1] } : { scale: 1 }}
      transition={playing ? { duration: 1.6, repeat: Infinity } : { duration: 0.2 }}
      className={`w-full text-left glass rounded-2xl p-4 border transition ${
        active ? "border-ocean-accent/60 shadow-lg shadow-ocean-accent/10" : "border-ocean-border/40 hover:border-ocean-border"
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        {/* Outcome label appears only once the right-side graph has revealed the
            outcome node for this decision; until then a neutral placeholder shows. */}
        {outcomeRevealed ? (
          <motion.span
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider"
            style={{ color: badge.color, border: `1px solid ${badge.color}55`, background: `${badge.color}14` }}
          >
            {badge.icon} {badge.label}
          </motion.span>
        ) : (
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wider text-gray-500 border border-dashed border-ocean-border/60">
            <Clock className="w-3 h-3" /> Awaiting outcome
          </span>
        )}
        {decision.confidence_score != null && (
          <span className="text-xs font-semibold text-ocean-accent">{decision.confidence_score}%</span>
        )}
      </div>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-gray-400 truncate">{dep.name || "—"}</span>
        <ChevronRight className="w-3.5 h-3.5 text-gray-600 shrink-0" />
        <span className="text-white font-semibold truncate">{decision.chosen_crew?.name || "—"}</span>
      </div>
      <div className="flex items-center justify-between mt-2 text-[11px] text-gray-500">
        <span>{decision.chosen_crew?.rank || dep.rank || "—"}</span>
        <span>{decision.created_at ? new Date(decision.created_at).toLocaleString() : ""}</span>
      </div>
    </motion.button>
  );
}

function TrajectoryTrace({ decision }: { decision: DecisionTrace }) {
  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
        <Activity className="w-4 h-4 text-ocean-accent" /> Agent Trajectory
        <span className="text-[10px] text-gray-500">({decision.trajectory.length} steps)</span>
      </h3>

      {/* L4 #4 — why this decision is still pending. */}
      {decision.outcome_status === "pending" && <PendingBanner decision={decision} />}

      {/* L4 #4 — the rejection-retry journey: each candidate compliance was run against. */}
      {decision.attempts && decision.attempts.length > 0 && (
        <ComplianceAttempts attempts={decision.attempts} />
      )}

      {/* Reasons + outcome reasons */}
      {decision.match_reasons?.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Why this candidate</p>
          <div className="flex flex-wrap gap-1.5">
            {decision.match_reasons.map((r, i) => (
              <span key={i} className="text-[11px] px-2 py-0.5 rounded-md bg-green-500/10 text-green-300 border border-green-500/20">{r}</span>
            ))}
          </div>
        </div>
      )}
      {decision.outcome_reasons?.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
            {decision.outcome_status === "rejected" ? "Rejection reasons" : "Conditions / warnings"}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {decision.outcome_reasons.map((r, i) => (
              <span key={i} className="text-[11px] px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-300 border border-amber-500/20">{r}</span>
            ))}
          </div>
        </div>
      )}

      {/* Step list */}
      <div className="space-y-1.5 max-h-[360px] overflow-y-auto pr-1">
        {decision.trajectory.length === 0 && (
          <p className="text-xs text-gray-500">No trajectory steps recorded.</p>
        )}
        {decision.trajectory.map((step, i) => <TrajectoryStep key={i} step={step} />)}
      </div>
    </div>
  );
}

function PendingBanner({ decision }: { decision: DecisionTrace }) {
  const reason =
    decision.pending_reason ||
    "Awaiting the compliance gate — the matched candidate has not yet been validated, so the placement outcome is still open.";
  return (
    <div className="mb-3 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5">
      <Clock className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
      <div>
        <p className="text-xs font-semibold text-amber-200">Why this is pending</p>
        <p className="text-[11px] text-gray-300 mt-0.5">{reason}</p>
      </div>
    </div>
  );
}

const ATTEMPT_STYLE: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  passed: { color: "#22c55e", icon: <CheckCircle className="w-3.5 h-3.5" />, label: "Passed" },
  warning: { color: "#f59e0b", icon: <AlertTriangle className="w-3.5 h-3.5" />, label: "Warning" },
  failed: { color: "#ef4444", icon: <XCircle className="w-3.5 h-3.5" />, label: "Failed" },
};

function ComplianceAttempts({ attempts }: { attempts: ComplianceAttempt[] }) {
  return (
    <div className="mb-3">
      <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 flex items-center gap-1.5">
        <RotateCcw className="w-3 h-3" /> Compliance attempts
        {attempts.length > 1 && (
          <span className="text-ocean-accent normal-case tracking-normal">
            · retried {attempts.length - 1}×
          </span>
        )}
      </p>
      <div className="space-y-1.5">
        {attempts.map((a, idx) => {
          const s = ATTEMPT_STYLE[a.compliance_status || ""] || ATTEMPT_STYLE.failed;
          const reasons = (a.compliance_status === "failed" ? a.failures : a.warnings) || [];
          const failed = a.compliance_status === "failed";
          const hasNext = idx < attempts.length - 1;
          return (
            <div key={`${a.order}-${a.crew_id}`}>
              <div className="rounded-lg border border-ocean-border/40 px-2.5 py-1.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[10px] text-gray-500 font-mono shrink-0">#{a.order}</span>
                    <span className="text-xs text-white truncate">{a.name || a.crew_id}</span>
                    {a.rank && <span className="text-[10px] text-gray-500 truncate">{a.rank}</span>}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0" style={{ color: s.color }}>
                    {a.compliance_score != null && (
                      <span className="text-[10px] text-gray-400">{a.compliance_score}%</span>
                    )}
                    <span className="flex items-center gap-1 text-[10px] font-semibold">{s.icon} {s.label}</span>
                  </div>
                </div>
                {reasons.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1 pl-6">
                    {reasons.map((r, i) => (
                      <span
                        key={i}
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{ color: s.color, background: `${s.color}12`, border: `1px solid ${s.color}30` }}
                      >
                        {r}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {/* Name the feedback: a rejection routes back to L3, which selects the next candidate. */}
              {failed && hasNext && (
                <div className="flex items-center gap-1.5 pl-3 py-0.5 text-[10px] text-red-300/80">
                  <RotateCcw className="w-3 h-3" />
                  rejected → feedback to L3 → selecting next-best candidate
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TrajectoryStep({ step }: { step: DecisionTrajectoryStep }) {
  if (step.kind === "agent") {
    return (
      <div className="flex items-center gap-2 mt-2 first:mt-0">
        <Cpu className="w-3.5 h-3.5 text-ocean-accent shrink-0" />
        <span className="text-xs font-semibold text-white">{step.agent_name}</span>
        {step.status && <span className="text-[10px] text-gray-500">· {step.status}</span>}
        {step.confidence_score != null && (
          <span className="text-[10px] text-green-400">· {(step.confidence_score * 100).toFixed(0)}%</span>
        )}
      </div>
    );
  }
  return (
    <div className="ml-5 pl-3 border-l border-ocean-border/40 py-1">
      <div className="flex items-center gap-1.5">
        <Wrench className="w-3 h-3 text-gray-400 shrink-0" />
        <span className="text-[11px] font-mono text-ocean-accent">{step.tool_name}</span>
        {step.duration_ms != null && <span className="text-[10px] text-gray-600">{step.duration_ms}ms</span>}
      </div>
      {step.input && (
        <p className="text-[10px] text-gray-500 font-mono mt-0.5 truncate" title={step.input}>
          → {step.input}
        </p>
      )}
      {step.output && (
        <p className="text-[10px] text-gray-400 font-mono mt-0.5 truncate" title={step.output}>
          ← {step.output}
        </p>
      )}
    </div>
  );
}

function NavLink({ href, icon, label, active = false }: { href: string; icon: React.ReactNode; label: string; active?: boolean }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all ${
        active
          ? "bg-ocean-accent/10 text-ocean-accent border border-ocean-accent/30"
          : "text-gray-400 hover:text-white hover:bg-ocean-border/30"
      }`}
    >
      {icon}
      {label}
    </Link>
  );
}
