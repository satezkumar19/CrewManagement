"use client";
/**
 * PatternPanel (L4 #4) — the bird's-eye view over the decision history.
 *
 * Aggregates INCREMENTALLY: it's fed only the decisions whose outcome has been
 * revealed so far (the walkthrough reveals them one crew member at a time), so the
 * counts, reject-rate, and the flagged RECURRING GAP all start at zero and build up
 * as each sign-off / sign-on outcome lands — rather than showing the full aggregate
 * up front. A completed pass yields the same result as the server's GET /patterns.
 */
import { useMemo } from "react";
import { AlertTriangle, Activity, ShieldCheck, TrendingDown, Lightbulb } from "lucide-react";

import { buildPatternReport } from "@/lib/patterns";
import type { DecisionTrace } from "@/types";

export default function PatternPanel({ decisions }: { decisions: DecisionTrace[] }) {
  const report = useMemo(() => buildPatternReport(decisions), [decisions]);

  const s = report.summary;
  const gap = report.recurring_gap;
  const cats = report.categories;
  const maxAffected = Math.max(1, ...cats.map((c) => c.decisions_affected));

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Activity className="w-4 h-4 text-ocean-accent" /> Pattern Detection
          <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-ocean-accent/15 text-ocean-accent border border-ocean-accent/30">L4</span>
        </h3>
        <div className="flex items-center gap-2 text-[11px]">
          <Chip label="Decisions" value={s.total} color="#94a3b8" />
          <Chip label="Signed on" value={s.signed_on} color="#22c55e" />
          <Chip label="Rejected" value={s.rejected} color="#ef4444" />
          <Chip label="Reject rate" value={`${s.rejection_rate}%`} color="#f59e0b" />
        </div>
      </div>

      {s.total === 0 ? (
        <p className="text-xs text-gray-500 py-2">
          No outcomes revealed yet — play the walkthrough or select a decision; the counts and recurring-gap build up as each crew member&apos;s outcome lands.
        </p>
      ) : (
        <>
          {/* The flagged recurring gap */}
          {gap ? (
            <div className="rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 mb-3">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="w-5 h-5 text-red-400 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-red-200">
                    Recurring gap detected: {gap.label}
                  </p>
                  <p className="text-[11px] text-gray-300 mt-0.5">
                    Blocked <span className="text-white font-semibold">{gap.decisions_affected}</span> placements
                    {gap.ports.length > 0 && <> across <span className="text-gray-200">{gap.ports.join(", ")}</span></>}
                    {gap.occurrences > gap.decisions_affected && <> · {gap.occurrences} total occurrences</>}.
                  </p>
                  {gap.examples.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {gap.examples.map((ex, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-300 border border-red-500/25">
                          {ex}
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="flex items-start gap-1.5 text-[11px] text-amber-200 mt-2">
                    <Lightbulb className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    <span>{gap.recommendation}</span>
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-xl border border-green-500/30 bg-green-500/5 px-3 py-2.5 mb-3">
              <ShieldCheck className="w-4 h-4 text-green-400 shrink-0" />
              <p className="text-[11px] text-gray-300">
                No recurring gap yet — no single failure category has blocked 2+ placements.
              </p>
            </div>
          )}

          {/* Category breakdown */}
          {cats.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 flex items-center gap-1.5">
                <TrendingDown className="w-3 h-3" /> Failure categories (by placements affected)
              </p>
              <div className="space-y-1.5">
                {cats.map((c) => {
                  const isGap = c.category === gap?.category;
                  const color = isGap ? "#ef4444" : "#64748b";
                  return (
                    <div key={c.category} className="flex items-center gap-3">
                      <span className="text-[11px] text-gray-300 w-44 shrink-0 truncate" title={c.label}>{c.label}</span>
                      <div className="flex-1 h-2 rounded-full bg-ocean-border/30 overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${(c.decisions_affected / maxAffected) * 100}%`, background: color }}
                        />
                      </div>
                      <span className="text-[10px] text-gray-400 w-24 shrink-0 text-right">
                        {c.decisions_affected} placement{c.decisions_affected === 1 ? "" : "s"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Chip({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <span
      className="px-2 py-0.5 rounded-md font-medium"
      style={{ color, border: `1px solid ${color}44`, background: `${color}12` }}
    >
      {label}: {value}
    </span>
  );
}
