"use client";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Trophy, CheckCircle2, ChevronDown, Sparkles, Users, Ship, FileText, Anchor,
} from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import { cn } from "@/lib/utils";
import CandidateReasoning from "@/components/intelligence/CandidateReasoning";
import AgentNarration from "@/components/intelligence/AgentNarration";
import type { IntelRankedCandidate, IntelInvestigatorReport } from "@/types";

const DIM = {
  crew: { label: "Crew", bar: "bg-sky-400", icon: Users },
  vessel: { label: "Vessel", bar: "bg-violet-400", icon: Ship },
  contract: { label: "Contract", bar: "bg-amber-400", icon: FileText },
} as const;

export default function ShortlistTab() {
  const { intel, setActiveTab } = useWorkflowStore();
  const result = intel.result;
  const candidates = result?.status === "matched" ? result.candidates : [];
  const top = candidates.find((c) => c.rank_position === 1) ?? candidates[0];
  const [expanded, setExpanded] = useState<string | null>(top?.crew_id ?? null);

  const ctx = (result?.context ?? {}) as { vacated_rank?: string; port?: string; vessel?: string };
  const subject = intel.subject;

  // ── Empty state — no L3 match has run yet ──────────────────────────────────────
  if (!candidates.length) {
    return (
      <div className="glass rounded-2xl p-12 flex flex-col items-center text-center gap-3">
        <div className="w-12 h-12 rounded-xl bg-accent-gradient/20 flex items-center justify-center">
          <Trophy className="w-6 h-6 text-ocean-accent" />
        </div>
        <p className="text-sm text-gray-300 font-medium">No shortlist yet</p>
        <p className="text-xs text-gray-500 max-w-sm">
          Go to the <b className="text-gray-300">Sign Off</b> tab and click{" "}
          <b className="text-gray-300">Initiate Sign Off</b> on a departing crew member —
          the agents analyse that candidate and shortlist replacements here, then sign on
          the #1 pick. (Or run an ad-hoc match from the <b className="text-gray-300">Intelligence Graph</b> panel.)
        </p>
      </div>
    );
  }

  return (
    <div className="glass rounded-2xl overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-ocean-border flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <Trophy className="w-4 h-4 text-amber-400" /> Top Selected Candidates
          </h2>
          {subject?.name ? (
            <p className="text-xs text-gray-500 mt-0.5">
              Replacements for signed-off{" "}
              <span className="text-gray-300 font-medium">{subject.name}</span>
              {" — "}{subject.rank ?? ctx.vacated_rank}
              {(subject.vessel ?? ctx.vessel) ? ` on ${subject.vessel ?? ctx.vessel}` : ""}
              {(subject.port ?? ctx.port) ? ` @ ${subject.port ?? ctx.port}` : ""}
              {" · "}{result?.pool_size ?? 0} assessed, {result?.disqualified ?? 0} filtered
            </p>
          ) : (
            <p className="text-xs text-gray-500 mt-0.5">
              {candidates.length} shortlisted for {ctx.vacated_rank ?? "vacancy"}
              {ctx.port ? ` · ${ctx.port}` : ""} · {result?.pool_size ?? 0} assessed,{" "}
              {result?.disqualified ?? 0} filtered
            </p>
          )}
        </div>
        <button
          onClick={() => setActiveTab("sign-off")}
          className="text-[10px] px-2.5 py-1.5 rounded-lg bg-ocean-card border border-ocean-border/50 text-gray-400 hover:text-white"
        >
          ← Sign-Off pool
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ocean-border bg-ocean/30">
              {["#", "Name", "Rank", "Port", "Score breakdown", "Fused", "Status", ""].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, idx) => {
              const isTop = c.rank_position === 1;
              const isSignedOn = intel.signedOnId === c.crew_id;
              const open = expanded === c.crew_id;
              return (
                <CandidateRows
                  key={c.crew_id}
                  c={c}
                  idx={idx}
                  isTop={isTop}
                  isSignedOn={isSignedOn}
                  open={open}
                  onToggle={() => setExpanded(open ? null : c.crew_id)}
                  reports={result?.reports}
                  top={top}
                  signingOn={isTop && intel.signingOn}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Managed-Agents live reasoning (async enrichment behind the fast result) */}
      <div className="px-4 pb-4 pt-1">
        <AgentNarration />
      </div>
    </div>
  );
}

function CandidateRows({
  c, idx, isTop, isSignedOn, open, onToggle, reports, top, signingOn,
}: {
  c: IntelRankedCandidate;
  idx: number;
  isTop: boolean;
  isSignedOn: boolean;
  open: boolean;
  onToggle: () => void;
  reports: IntelInvestigatorReport[] | undefined;
  top?: IntelRankedCandidate;
  signingOn: boolean;
}) {
  return (
    <>
      <motion.tr
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: idx * 0.04 }}
        onClick={onToggle}
        className={cn(
          "crew-row border-b border-ocean-border/30 cursor-pointer transition-all",
          isTop ? "bg-emerald-900/15 border-emerald-500/30" : "hover:bg-ocean-border/10",
        )}
      >
        <td className="px-4 py-3">
          <span className={cn(
            "w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold",
            isTop ? "bg-amber-400/20 text-amber-300" : "bg-ocean-border/50 text-gray-300",
          )}>
            {c.rank_position}
          </span>
        </td>
        <td className="px-4 py-3">
          <span className={cn("font-medium", isTop ? "text-emerald-200" : "text-white")}>{c.name}</span>
        </td>
        <td className="px-4 py-3 text-gray-300 text-xs whitespace-nowrap">{c.rank}</td>
        <td className="px-4 py-3 text-gray-400 text-xs">{c.port}</td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {(["crew", "vessel", "contract"] as const).map((k) => {
              const v = c.dimension_scores[k] ?? 0;
              return (
                <div key={k} className="w-14" title={`${DIM[k].label}: ${Math.round(v * 100)}`}>
                  <div className="flex items-center justify-between text-[8px] text-gray-500">
                    <span>{DIM[k].label}</span><span className="tabular-nums">{Math.round(v * 100)}</span>
                  </div>
                  <div className="h-1 rounded-full bg-ocean-border/40 mt-0.5 overflow-hidden">
                    <div className={cn("h-full rounded-full", DIM[k].bar)} style={{ width: `${v * 100}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </td>
        <td className="px-4 py-3">
          <span className={cn("text-sm font-semibold tabular-nums", isTop ? "text-emerald-300" : "text-ocean-accent")}>
            {c.score}
          </span>
        </td>
        <td className="px-4 py-3">
          {isTop ? (
            signingOn ? (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-ocean-accent/15 text-ocean-accent text-[10px] border border-ocean-accent/30">
                <span className="w-2.5 h-2.5 border-2 border-ocean-accent border-t-transparent rounded-full animate-spin" />
                Signing on…
              </span>
            ) : isSignedOn ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px] border border-emerald-500/30">
                <CheckCircle2 className="w-3 h-3" /> Signed On · agent
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300 text-[10px] border border-emerald-500/30">
                <Sparkles className="w-3 h-3" /> Selected
              </span>
            )
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-300 text-[10px] border border-amber-500/25">
              <Anchor className="w-3 h-3" /> Fallback
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-right">
          <ChevronDown className={cn("w-4 h-4 text-gray-500 transition-transform", open && "rotate-180")} />
        </td>
      </motion.tr>

      {/* Expandable explainability row */}
      <AnimatePresence initial={false}>
        {open && (
          <tr>
            <td colSpan={8} className="p-0 border-b border-ocean-border/30">
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="px-6 py-3 bg-ocean-card/30">
                  <CandidateReasoning candidate={c} reports={reports} top={top} signedOn={isSignedOn} />
                </div>
              </motion.div>
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}
