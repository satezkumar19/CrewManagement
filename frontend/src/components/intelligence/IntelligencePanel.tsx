"use client";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Network, Sparkles, Loader2, Trophy, AlertTriangle,
  Mail, MessageSquare, Hash, Users, FileText, Ship, ChevronRight, ChevronDown,
} from "lucide-react";

import { useWorkflowStore } from "@/store/workflowStore";
import { runIntelByContext } from "@/lib/runIntelMatch";
import type { IntelRankedCandidate, IntelResult } from "@/types";
import IntelligenceFlow from "./IntelligenceFlow";
import IntelligenceGraph from "./IntelligenceGraph";
import CandidateReasoning from "./CandidateReasoning";
import AgentNarration from "./AgentNarration";

const RANKS = [
  "Master", "Chief Officer", "Second Officer", "Third Officer",
  "Chief Engineer", "Second Engineer", "Third Engineer", "Bosun", "AB Seaman",
  "Pumpman",  // demo: live pool has 3 Pumpmen, all ineligible → no-crew-found
];
const PORTS = ["Singapore", "Rotterdam", "Houston", "Dubai", "Shanghai", "Mumbai", "Piraeus", "Manila"];

// Per-dimension colours for the mini score bars + investigator chips.
const DIM = {
  crew: { label: "Crew Intel", color: "bg-sky-400", text: "text-sky-300", icon: Users },
  vessel: { label: "Vessel Ops", color: "bg-violet-400", text: "text-violet-300", icon: Ship },
  contract: { label: "Contract/Wage", color: "bg-amber-400", text: "text-amber-300", icon: FileText },
} as const;

const CHANNEL_ICON: Record<string, typeof Mail> = { email: Mail, sms: MessageSquare, slack: Hash };

export default function IntelligencePanel() {
  const { intel } = useWorkflowStore();
  const [rank, setRank] = useState("Chief Officer");
  const [port, setPort] = useState("Singapore");

  const run = () => runIntelByContext(rank, port);

  const r = intel.result;

  return (
    <div className="glass rounded-2xl p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-accent-gradient flex items-center justify-center">
            <Network className="w-4 h-4 text-white" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">Intelligence Graph</h3>
            <p className="text-[10px] text-gray-500 -mt-0.5">L3 · Supervisor → 3 investigators → ranked shortlist</p>
          </div>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded-full bg-ocean-border/40 text-gray-400">L3</span>
      </div>

      {/* Controls — pick a vacancy, run the match */}
      <div className="flex items-end gap-2 mb-3">
        <label className="flex-1 text-[10px] text-gray-400">
          Vacated rank
          <select value={rank} onChange={(e) => setRank(e.target.value)}
            className="mt-1 w-full bg-ocean-card border border-ocean-border rounded-lg px-2 py-1.5 text-xs text-white">
            {RANKS.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label className="flex-1 text-[10px] text-gray-400">
          Join port
          <select value={port} onChange={(e) => setPort(e.target.value)}
            className="mt-1 w-full bg-ocean-card border border-ocean-border rounded-lg px-2 py-1.5 text-xs text-white">
            {PORTS.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <button onClick={run} disabled={intel.running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-gradient text-white text-xs font-medium disabled:opacity-50">
          {intel.running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
          {intel.running ? "Running" : "Run Match"}
        </button>
      </div>

      {/* Graphical workflow pipeline (Input → Supervisor → 3 investigators → Fusion → Output) */}
      <div className="mb-3">
        <IntelligenceFlow />
      </div>

      {/* Derived L3 fit graph (vacancy → candidates → dimensions → L2 facts), live */}
      <div className="mb-3">
        <IntelligenceGraph />
      </div>

      {/* Live workflow trace (built from streamed intel_* events) */}
      {intel.trace.length > 0 && (
        <div className="mb-3 rounded-lg bg-ocean-card/60 border border-ocean-border/40 p-2 max-h-36 overflow-y-auto">
          <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Workflow stream</p>
          <div className="space-y-1">
            <AnimatePresence initial={false}>
              {intel.trace.map((step, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                  className="flex items-center gap-2 text-[11px]">
                  <span className="text-gray-600 tabular-nums w-12 shrink-0">t+{step.t}ms</span>
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotFor(step.type)}`} />
                  <span className="text-gray-300 truncate">{step.label}</span>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </div>
      )}

      {/* Managed-Agents live reasoning (streams in behind the fast result) */}
      <div className="mb-3">
        <AgentNarration />
      </div>

      {/* Result */}
      {r && r.status === "matched" && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="flex items-center gap-1.5 text-xs text-emerald-300">
              <Trophy className="w-3.5 h-3.5" /> Top {r.candidates.length} candidate{r.candidates.length === 1 ? "" : "s"}
            </span>
            <span className="text-[10px] text-gray-500">
              {r.pool_size} assessed · {r.disqualified} filtered · {r.timing.total_ms}ms
            </span>
          </div>

          <div className="space-y-2">
            {r.candidates.map((c, i) => (
              <CandidateCard
                key={c.crew_id}
                c={c}
                index={i}
                reports={r.reports}
                top={r.candidates.find((x) => x.rank_position === 1) ?? r.candidates[0]}
                signedOn={intel.signedOnId === c.crew_id}
              />
            ))}
          </div>

          {r.notifications.length > 0 && (
            <div className="mt-3">
              <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1.5">Operators notified</p>
              <div className="flex flex-wrap gap-1.5">
                {r.notifications.map((n, i) => {
                  const Icon = CHANNEL_ICON[n.channel] || Mail;
                  return (
                    <span key={i}
                      className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-ocean-card border border-ocean-border/50 text-[10px] text-gray-300">
                      <Icon className="w-3 h-3 text-ocean-accent" />
                      {n.role}
                      <span className="text-gray-600">·</span>
                      <span className={n.status === "delivered" ? "text-emerald-400" : "text-red-400"}>{n.channel}</span>
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* No-crew-found graceful state */}
      {r && r.status === "no_crew_found" && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-3">
          <div className="flex items-center gap-2 text-amber-300 text-xs font-medium mb-1">
            <AlertTriangle className="w-4 h-4" /> No eligible crew
          </div>
          <p className="text-[11px] text-amber-200/80">{r.message}</p>
          {r.notifications.length > 0 && (
            <p className="text-[10px] text-gray-400 mt-2">
              Escalated to {r.notifications.map((n) => n.role).join(", ")}.
            </p>
          )}
        </div>
      )}

      {r && r.status === "error" && (
        <p className="text-[11px] text-red-400">{r.message}</p>
      )}

      {!intel.trace.length && !r && (
        <p className="text-[11px] text-gray-500 text-center py-3">
          Pick a vacancy and run the match to see the supervisor delegate to all three investigators.
        </p>
      )}
    </div>
  );
}

function CandidateCard({
  c, index, reports, top, signedOn,
}: {
  c: IntelRankedCandidate;
  index: number;
  reports?: IntelResult["reports"];
  top?: IntelRankedCandidate;
  signedOn?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.06 }}
      className="rounded-lg bg-ocean-card/70 border border-ocean-border/50 p-2.5">
      <button onClick={() => setOpen((v) => !v)} className="w-full text-left">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 ${
              c.rank_position === 1 ? "bg-amber-400/20 text-amber-300" : "bg-ocean-border/50 text-gray-300"}`}>
              {c.rank_position}
            </span>
            <div className="min-w-0">
              <p className="text-xs font-medium text-white truncate flex items-center gap-1.5">
                {c.name}
                {signedOn && <span className="text-[8px] px-1 py-px rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">signed on</span>}
              </p>
              <p className="text-[10px] text-gray-500 truncate">{c.rank} · {c.port}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-semibold text-ocean-accent tabular-nums">{c.score}</span>
            <ChevronDown className={`w-3.5 h-3.5 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`} />
          </div>
        </div>

        {/* Per-dimension mini score bars */}
        <div className="flex gap-2 mt-2">
          {(["crew", "vessel", "contract"] as const).map((k) => {
            const v = c.dimension_scores[k] ?? 0;
            return (
              <div key={k} className="flex-1">
                <div className="flex items-center justify-between text-[9px] text-gray-500">
                  <span>{DIM[k].label}</span><span className="tabular-nums">{Math.round(v * 100)}</span>
                </div>
                <div className="h-1 rounded-full bg-ocean-border/40 mt-0.5 overflow-hidden">
                  <div className={`h-full rounded-full ${DIM[k].color}`} style={{ width: `${v * 100}%` }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* Rationale (collapsed summary) */}
        {!open && c.rationale.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {c.rationale.slice(0, 3).map((reason, i) => (
              <li key={i} className="flex items-start gap-1 text-[10px] text-gray-400">
                <ChevronRight className="w-3 h-3 mt-px text-ocean-accent shrink-0" />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        )}
      </button>

      {/* Full explainability (reasons + sources + why-lower-than-#1) */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="pt-2.5">
              <CandidateReasoning candidate={c} reports={reports} top={top} signedOn={signedOn} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function dotFor(type: string): string {
  if (type.includes("supervisor_started")) return "bg-ocean-accent";
  if (type.includes("investigator")) return "bg-sky-400";
  if (type.includes("ranking")) return "bg-emerald-400";
  if (type.includes("no_crew")) return "bg-amber-400";
  if (type.includes("notification")) return "bg-violet-400";
  if (type.includes("completed")) return "bg-emerald-500";
  return "bg-gray-500";
}
