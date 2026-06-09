"use client";
import {
  Users, Ship, FileText, CheckCircle2, ArrowDownRight, Trophy,
} from "lucide-react";
import type {
  IntelRankedCandidate, IntelInvestigatorReport, IntelAssessment,
} from "@/types";

// Fusion weights — MUST mirror backend ranking.WEIGHTS (crew 50 / vessel 30 / contract 20).
const WEIGHTS = { crew: 0.5, vessel: 0.3, contract: 0.2 } as const;
type DimKey = keyof typeof WEIGHTS;

const DIM: Record<DimKey, { label: string; color: string; bar: string; text: string; icon: typeof Users }> = {
  crew: { label: "Crew Intel", color: "border-sky-500/40", bar: "bg-sky-400", text: "text-sky-300", icon: Users },
  vessel: { label: "Vessel Ops", color: "border-violet-500/40", bar: "bg-violet-400", text: "text-violet-300", icon: Ship },
  contract: { label: "Contract/Wage", color: "border-amber-500/40", bar: "bg-amber-400", text: "text-amber-300", icon: FileText },
};
const DIM_ORDER: DimKey[] = ["crew", "vessel", "contract"];

// Map an investigator's name to its dimension key (reports are a flat list).
function reportKey(name: string): DimKey | null {
  const n = (name || "").toLowerCase();
  if (n.includes("crew")) return "crew";
  if (n.includes("contract") || n.includes("wage")) return "contract";
  if (n.includes("vessel")) return "vessel";
  return null;
}

function assessmentFor(
  reports: IntelInvestigatorReport[] | undefined, dim: DimKey, crewId: string,
): { a?: IntelAssessment; applied?: Record<string, unknown> } {
  const report = (reports || []).find((r) => reportKey(r.investigator) === dim);
  return { a: report?.assessments?.[crewId], applied: report?.applied };
}

// ── humanize signals / applied rules into "source" chips ────────────────────────
function humanizeKey(k: string): string {
  return k
    .replace(/^l2_/, "")
    .replace(/_usd$/, " (USD)")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// Which signals/applied keys to surface per dimension, and which are L2-graph facts.
const SOURCE_KEYS: Record<DimKey, { signals: string[]; applied: string[]; l2: string[] }> = {
  crew: {
    signals: ["available", "rank", "rank_distance", "stcw_status", "missing_base_certs", "experience_years", "l2_required_safety_certs"],
    applied: [],
    l2: ["l2_required_safety_certs", "l2_missing_safety_certs"],
  },
  vessel: {
    signals: ["required_certs", "missing_required_certs", "nationality", "candidate_port", "join_port", "experience_years"],
    applied: ["join_by", "departure_window_days", "l2_port_restricted_nationalities", "min_experience_years"],
    l2: ["l2_port_restricted_nationalities"],
  },
  contract: {
    signals: ["expected_wage_usd", "period_months"],
    applied: ["wage_band_usd", "contract_rules"],
    l2: [],
  },
};

interface SourceChip { key: string; label: string; value: string; isL2: boolean }

function sourcesFor(dim: DimKey, a?: IntelAssessment, applied?: Record<string, unknown>): SourceChip[] {
  const spec = SOURCE_KEYS[dim];
  const chips: SourceChip[] = [];
  const seen = new Set<string>();
  const push = (k: string, src?: Record<string, unknown>) => {
    if (!src || !(k in src) || seen.has(k)) return;
    seen.add(k);
    chips.push({ key: k, label: humanizeKey(k), value: formatValue(src[k]), isL2: spec.l2.includes(k) });
  };
  spec.signals.forEach((k) => push(k, a?.signals));
  spec.applied.forEach((k) => push(k, applied));
  return chips;
}

export default function CandidateReasoning({
  candidate, reports, top, signedOn = false,
}: {
  candidate: IntelRankedCandidate;
  reports?: IntelInvestigatorReport[];
  top?: IntelRankedCandidate;            // the rank-1 candidate (for the fallback delta)
  signedOn?: boolean;
}) {
  const isTop = candidate.rank_position === 1 || !top || top.crew_id === candidate.crew_id;

  return (
    <div className="space-y-2.5">
      {/* Headline: selected vs fallback */}
      <div className={`rounded-lg px-3 py-2 border text-[11px] ${
        isTop ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-200"
              : "bg-amber-500/10 border-amber-500/30 text-amber-200"}`}>
        {isTop ? (
          <span className="flex items-center gap-1.5">
            <Trophy className="w-3.5 h-3.5 text-emerald-300" />
            <b className="text-emerald-100">Selected</b> — highest fused score
            {signedOn && <span className="ml-1 text-emerald-300">· signed on by the agent</span>}
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            <ArrowDownRight className="w-3.5 h-3.5 text-amber-300" />
            <b className="text-amber-100">Fallback #{candidate.rank_position}</b>
            {top && <span> — {(top.score - candidate.score).toFixed(1)} pts behind #1 {top.name}</span>}
          </span>
        )}
      </div>

      {/* Fused-score formula */}
      <div className="text-[10px] text-gray-500">
        Fused score <span className="text-gray-300 font-semibold tabular-nums">{candidate.score}</span>
        {" = "}
        {DIM_ORDER.map((k, i) => {
          const contrib = (candidate.dimension_scores[k] ?? 0) * WEIGHTS[k] * 100;
          return (
            <span key={k}>
              {i > 0 && " + "}
              <span className={DIM[k].text}>{(WEIGHTS[k] * 100).toFixed(0)}%·{DIM[k].label}</span>
              {" "}<span className="text-gray-400 tabular-nums">({contrib.toFixed(1)})</span>
            </span>
          );
        })}
      </div>

      {/* Per-dimension breakdown */}
      <div className="space-y-2">
        {DIM_ORDER.map((dim) => {
          const { a, applied } = assessmentFor(reports, dim, candidate.crew_id);
          const score01 = candidate.dimension_scores[dim] ?? a?.score ?? 0;
          const contrib = score01 * WEIGHTS[dim] * 100;
          const sources = sourcesFor(dim, a, applied);
          const Icon = DIM[dim].icon;
          return (
            <div key={dim} className={`rounded-lg bg-ocean-card/60 border ${DIM[dim].color} p-2.5`}>
              <div className="flex items-center justify-between mb-1.5">
                <span className={`flex items-center gap-1.5 text-[11px] font-medium ${DIM[dim].text}`}>
                  <Icon className="w-3.5 h-3.5" /> {DIM[dim].label}
                  <span className="text-gray-600">· weight {(WEIGHTS[dim] * 100).toFixed(0)}%</span>
                </span>
                <span className="text-[10px] text-gray-400 tabular-nums">
                  {Math.round(score01 * 100)}/100 → <span className="text-gray-200">+{contrib.toFixed(1)} pts</span>
                </span>
              </div>
              <div className="h-1 rounded-full bg-ocean-border/40 overflow-hidden mb-2">
                <div className={`h-full rounded-full ${DIM[dim].bar}`} style={{ width: `${score01 * 100}%` }} />
              </div>

              {/* Reasons — the "why" */}
              {a?.reasons?.length ? (
                <ul className="space-y-0.5 mb-2">
                  {a.reasons.map((reason, i) => (
                    <li key={i} className="flex items-start gap-1 text-[10px] text-gray-300">
                      <CheckCircle2 className="w-3 h-3 mt-px text-emerald-400 shrink-0" />
                      <span>{reason}</span>
                    </li>
                  ))}
                </ul>
              ) : null}

              {/* Sources / facts consulted */}
              {sources.length > 0 && (
                <div>
                  <p className="text-[9px] uppercase tracking-wide text-gray-600 mb-1">Sources / facts consulted</p>
                  <div className="flex flex-wrap gap-1">
                    {sources.map((s) => (
                      <span key={s.key}
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] border ${
                          s.isL2 ? "bg-violet-500/10 border-violet-500/30 text-violet-200"
                                 : "bg-ocean-border/30 border-ocean-border/50 text-gray-400"}`}>
                        {s.isL2 && <span className="text-[8px] font-semibold text-violet-300">L2</span>}
                        <span className="text-gray-500">{s.label}:</span>
                        <span className="text-gray-300">{s.value}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Why ranked lower than #1 (fallback only) */}
      {!isTop && top && (
        <FallbackDelta candidate={candidate} top={top} reports={reports} />
      )}
    </div>
  );
}

function FallbackDelta({
  candidate, top, reports,
}: {
  candidate: IntelRankedCandidate;
  top: IntelRankedCandidate;
  reports?: IntelInvestigatorReport[];
}) {
  // Per-dimension point gap vs #1, largest first.
  const gaps = DIM_ORDER.map((dim) => {
    const topS = top.dimension_scores[dim] ?? 0;
    const candS = candidate.dimension_scores[dim] ?? 0;
    const pts = (topS - candS) * WEIGHTS[dim] * 100;
    // Reasons #1 had that this candidate did not (the edge that cost points).
    const topReasons = assessmentFor(reports, dim, top.crew_id).a?.reasons ?? [];
    const candReasons = new Set(assessmentFor(reports, dim, candidate.crew_id).a?.reasons ?? []);
    const advantage = topReasons.filter((r) => !candReasons.has(r));
    return { dim, pts, advantage };
  })
    .filter((g) => g.pts > 0.05)
    .sort((a, b) => b.pts - a.pts);

  if (!gaps.length) {
    return (
      <div className="rounded-lg bg-ocean-card/60 border border-ocean-border/50 p-2.5 text-[10px] text-gray-400">
        Scores tie on every dimension; #{candidate.rank_position} is ordered after #1 by the
        stable crew-id tiebreak.
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-amber-500/5 border border-amber-500/25 p-2.5">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-200 mb-1.5">
        <ArrowDownRight className="w-3.5 h-3.5" /> Why ranked below #1 {top.name}
      </p>
      <ul className="space-y-1.5">
        {gaps.map((g) => (
          <li key={g.dim} className="text-[10px] text-gray-300">
            <span className="text-amber-300 font-semibold tabular-nums">−{g.pts.toFixed(1)} pts</span>
            {" on "}<span className={DIM[g.dim].text}>{DIM[g.dim].label}</span>
            {g.advantage.length > 0 && (
              <span className="text-gray-500"> — #1 led with: {g.advantage.join("; ")}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export { CandidateReasoning };
