"use client";
/**
 * PrecedentPanel (L4 #2) — shows what the Precedent Index returned when this
 * decision's matching query started. If prior placements existed for the same
 * vacancy profile (rank @ port), it's a REPEAT query and they're listed with
 * their outcomes; otherwise it's the first placement for that profile.
 *
 * Reads `decision.consulted_precedents`, which the backend captured at sign-off
 * start (the lookup performed before matching) — so this is the visible proof
 * the index was "consulted on the 2nd+ query".
 */
import { History, CheckCircle, XCircle, Repeat, Sparkle, TrendingUp, ArrowUpDown, Zap, UserCheck } from "lucide-react";
import type { DecisionTrace } from "@/types";

const HUMAN_COLOR = "#a78bfa";

const OUTCOME_COLOR: Record<string, string> = {
  signed_on: "#22c55e",
  rejected: "#ef4444",
};

export default function PrecedentPanel({ decision }: { decision: DecisionTrace }) {
  const consult = decision.consulted_precedents;
  const q = consult?.query || {
    rank: decision.query_context?.departing_crew?.rank,
    port: decision.query_context?.departing_crew?.port,
  };
  const profile = [q?.rank, q?.port].filter(Boolean).join(" @ ");
  const isRepeat = !!decision.is_repeat_query && (consult?.matches?.length ?? 0) > 0;

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <History className="w-4 h-4 text-ocean-accent" /> Precedent Index
        </h3>
        {profile && (
          <span className="text-[10px] text-gray-500">consulted for <span className="text-gray-300">{profile}</span></span>
        )}
      </div>

      {/* L4 #3 — how the consulted precedent fed back into L3's ranking. Shown for
          EVERY decision (signed / rejected / pending): the applied case shows the
          lift + rerank; otherwise an explicit "no re-rank" note so the feedback
          channel is always visible. */}
      {decision.precedent_feedback?.applied ? (
        <FeedbackStrip decision={decision} />
      ) : (
        <div className="mb-3 flex items-center gap-2 rounded-xl border border-ocean-border/40 bg-ocean-border/10 px-3 py-2">
          <Zap className="w-3.5 h-3.5 text-gray-500 shrink-0" />
          <p className="text-[11px] text-gray-400">
            <span className="text-gray-300 font-medium">Precedent feedback → L3:</span>{" "}
            no re-rank applied —{" "}
            {isRepeat
              ? "prior placements existed but did not change the ranking."
              : "first placement for this profile, base ranking used."}
          </p>
        </div>
      )}

      {isRepeat ? (
        <RepeatView decision={decision} />
      ) : (
        <div className="flex items-start gap-2 rounded-xl border border-dashed border-ocean-border/60 px-3 py-3">
          <Sparkle className="w-4 h-4 text-violet-300 mt-0.5 shrink-0" />
          <div>
            <p className="text-xs text-gray-200 font-medium">First placement for this profile</p>
            <p className="text-[11px] text-gray-500 mt-0.5">
              No prior history for {profile || "this vacancy"} — this decision becomes the precedent the next query will find.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function FeedbackStrip({ decision }: { decision: DecisionTrace }) {
  const fb = decision.precedent_feedback!;
  const lift = fb.lift ?? 0;
  const hasLift = lift > 0;
  return (
    <div className="mb-3 rounded-xl border border-ocean-accent/30 bg-ocean-accent/5 px-3 py-2.5">
      <div className="flex items-center justify-between mb-1.5">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ocean-accent">
          <Zap className="w-3.5 h-3.5" /> Precedent feedback → L3
        </span>
        <div className="flex items-center gap-1.5">
          {hasLift && (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold text-green-300 bg-green-500/10 border border-green-500/25">
              <TrendingUp className="w-3 h-3" /> +{lift.toFixed(1)}% confidence
            </span>
          )}
          {fb.reranked && (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold text-violet-200 bg-violet-500/10 border border-violet-400/30">
              <ArrowUpDown className="w-3 h-3" /> re-ranked winner
            </span>
          )}
        </div>
      </div>

      {/* What the boost did to the selection */}
      {fb.reranked && fb.base_winner && fb.adjusted_winner ? (
        <p className="text-[11px] text-gray-300">
          Base ranking led with <span className="text-gray-400">{fb.base_winner.name}</span>; precedent
          boost promoted <span className="text-white font-medium">{fb.adjusted_winner.name}</span> to the top
          {fb.top_base_score != null && fb.top_adjusted_score != null && (
            <span className="text-gray-500"> ({fb.top_base_score}% → {fb.top_adjusted_score}%)</span>
          )}.
        </p>
      ) : (
        <p className="text-[11px] text-gray-300">
          Precedent boost raised the selected candidate&apos;s confidence
          {fb.top_base_score != null && fb.top_adjusted_score != null && (
            <span className="text-gray-500"> from {fb.top_base_score}% to {fb.top_adjusted_score}%</span>
          )} — selection unchanged.
        </p>
      )}

      {fb.rationale && (
        <p className="text-[10px] text-gray-500 mt-1 italic">{fb.rationale}</p>
      )}

      {/* Boosted candidates */}
      {fb.boosted && fb.boosted.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {fb.boosted.map((b) => (
            <span
              key={b.crew_id}
              className="text-[10px] px-2 py-0.5 rounded-md bg-ocean-accent/10 text-ocean-accent border border-ocean-accent/25"
            >
              {b.name || b.crew_id}{b.nationality ? ` · ${b.nationality}` : ""}{" "}
              <span className="font-semibold">{b.boost >= 0 ? "+" : ""}{b.boost.toFixed(1)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RepeatView({ decision }: { decision: DecisionTrace }) {
  const consult = decision.consulted_precedents!;
  const s = consult.summary;
  return (
    <div className="space-y-3">
      {/* Repeat banner + summary */}
      <div className="flex items-center gap-2 rounded-xl bg-ocean-accent/10 border border-ocean-accent/30 px-3 py-2">
        <Repeat className="w-4 h-4 text-ocean-accent shrink-0" />
        <p className="text-xs text-gray-100">
          <span className="font-semibold text-ocean-accent">Repeat query</span> — {s.total} prior placement{s.total === 1 ? "" : "s"} for this profile
        </p>
      </div>

      <div className="flex flex-wrap gap-2 text-[11px]">
        <Stat label="Signed on" value={s.signed_on} color="#22c55e" />
        <Stat label="Rejected" value={s.rejected} color="#ef4444" />
        {!!s.human_reviewed && s.human_reviewed > 0 && (
          <Stat label="Human-reviewed" value={s.human_reviewed} color={HUMAN_COLOR} />
        )}
        {s.avg_compliance_score != null && (
          <Stat label="Avg compliance" value={`${s.avg_compliance_score}%`} color="#00d4ff" />
        )}
      </div>

      {/* Prior placements list */}
      <div className="space-y-1.5">
        <p className="text-[10px] uppercase tracking-wider text-gray-500">Prior placements</p>
        {consult.matches.map((m) => {
          const color = OUTCOME_COLOR[m.outcome_status || ""] || "#94a3b8";
          return (
            <div key={m.precedent_id} className="flex items-center justify-between rounded-lg border border-ocean-border/40 px-2.5 py-1.5">
              <div className="flex items-center gap-2 min-w-0">
                {m.outcome_status === "signed_on" ? (
                  <CheckCircle className="w-3.5 h-3.5 shrink-0" style={{ color }} />
                ) : (
                  <XCircle className="w-3.5 h-3.5 shrink-0" style={{ color }} />
                )}
                <span className="text-xs text-white truncate">{m.chosen_crew_name || m.chosen_crew_id}</span>
                <span className="text-[10px] text-gray-500 truncate">{m.chosen_crew_rank}</span>
                {m.human_reviewed && (
                  <span
                    className="flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded shrink-0"
                    style={{ color: HUMAN_COLOR, border: `1px solid ${HUMAN_COLOR}55`, background: `${HUMAN_COLOR}14` }}
                    title={m.reviewed_by ? `Human-reviewed by ${m.reviewed_by}` : "Human-reviewed"}
                  >
                    <UserCheck className="w-2.5 h-2.5" /> Human
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {m.compliance_score != null && (
                  <span className="text-[10px] text-gray-400">{m.compliance_score}%</span>
                )}
                <span className="text-[10px] font-semibold" style={{ color }}>
                  {m.outcome_status === "signed_on" ? "Signed on" : "Rejected"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <span
      className="px-2 py-0.5 rounded-md font-medium"
      style={{ color, border: `1px solid ${color}44`, background: `${color}12` }}
    >
      {label}: {value}
    </span>
  );
}
