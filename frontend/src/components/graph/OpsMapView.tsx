"use client";
/**
 * OpsMapView — the L2 OpsMap (process-mining) dimension. Self-contained: fetches the
 * mined process model + variants + bottlenecks + conformance from the OpsMap API and
 * renders the directly-follows graph alongside variant / bottleneck / conformance
 * panels. The log fills as crew-change workflows run, so it offers a refresh and an
 * empty state that points the user at the Dashboard.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  Activity, RefreshCw, Loader2, GitBranch, Timer, CheckCircle2, Route,
  AlertTriangle, XCircle, X, ArrowRight, ArrowLeft, Zap, User, Users, Ship,
  Workflow, Compass,
} from "lucide-react";

import {
  opsMapApi,
  type OpsMapProcess, type OpsMapSummary, type OpsMapVariants,
  type OpsMapBottlenecks, type OpsMapConformance, type OpsMapOutcome,
  type OpsMapCases, type OpsMapCase, type OpsMapCaseStep,
} from "@/lib/api";
import OpsMapGraph from "./OpsMapGraph";

const OUTCOME_STYLE: Record<OpsMapOutcome, { color: string; label: string }> = {
  success: { color: "#10b981", label: "Signed On" },
  rejected: { color: "#f59e0b", label: "Rejected" },
  failed: { color: "#ef4444", label: "Failed" },
  in_progress: { color: "#94a3b8", label: "In progress" },
};

// Static per-activity metadata: what workflow event the activity is mined from and
// which agent emits it (mirrors the OpsMap activity vocabulary in OPSMAP_DESIGN.md).
const ACTIVITY_META: Record<string, { event: string; actor: string; about: string }> = {
  "Sign-Off Initiated": {
    event: "workflow_created", actor: "Master Agent",
    about: "The coordinator opens a session and the crew-change case begins.",
  },
  "Crew Matching": {
    event: "agent_completed", actor: "Crew Matching Agent",
    about: "A relief candidate is matched against the departing crew member.",
  },
  "Travel Arranged": {
    event: "agent_completed", actor: "Travel Agent",
    about: "Flights / logistics for the crew change are arranged.",
  },
  "Crew Notified": {
    event: "agent_completed", actor: "Notification Agent",
    about: "Crew and stakeholders are notified of the planned change.",
  },
  "Sign-Off Confirmed": {
    event: "crew_updated", actor: "Master Agent",
    about: "The departing crew member is confirmed signed off.",
  },
  "Compliance Check": {
    event: "auto_compliance / sign_on_initiated", actor: "Master / Compliance Agent",
    about: "The incoming crew's documents are validated against port rules.",
  },
  "Signed On": {
    event: "crew_signed_on", actor: "Compliance Agent",
    about: "Compliance cleared — the relief crew is signed on. Terminal (success).",
  },
  "Sign-On Rejected": {
    event: "sign_on_rejected", actor: "Compliance Agent",
    about: "Compliance failed — the sign-on is rejected. Terminal (rejected).",
  },
  "Workflow Failed": {
    event: "workflow_failed", actor: "Master Agent",
    about: "The workflow errored before completing. Terminal (failed).",
  },
};

// Which process map is on screen: the mined ("discovered") model or the designed
// ("reference") one. The reference map always shows the full flow — even with 0 cases.
type MapMode = "discovered" | "reference";

export default function OpsMapView() {
  const [process, setProcess] = useState<OpsMapProcess | null>(null);
  const [reference, setReference] = useState<OpsMapProcess | null>(null);
  const [summary, setSummary] = useState<OpsMapSummary | null>(null);
  const [variants, setVariants] = useState<OpsMapVariants | null>(null);
  const [bottlenecks, setBottlenecks] = useState<OpsMapBottlenecks | null>(null);
  const [conformance, setConformance] = useState<OpsMapConformance | null>(null);
  const [cases, setCases] = useState<OpsMapCases | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [mapMode, setMapMode] = useState<MapMode>("discovered");
  const didAutoSwitch = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, r, s, v, b, c, cs] = await Promise.all([
        opsMapApi.getProcess(),
        opsMapApi.getReference(),
        opsMapApi.getSummary(),
        opsMapApi.getVariants(),
        opsMapApi.getBottlenecks(5),
        opsMapApi.getConformance(),
        opsMapApi.getCases(),
      ]);
      setProcess(p); setReference(r); setSummary(s);
      setVariants(v); setBottlenecks(b); setConformance(c); setCases(cs);
      // First load with no mined cases: open on the reference (designed) flow so the
      // page always shows the process, not an empty canvas. Respects a later manual pick.
      if (!didAutoSwitch.current) {
        didAutoSwitch.current = true;
        if (p.metrics.total_cases === 0) setMapMode("reference");
      }
    } catch {
      toast.error("Failed to load OpsMap");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const empty = !!process && process.metrics.total_cases === 0;
  const showReference = mapMode === "reference";
  const topBottleneck = bottlenecks?.bottlenecks[0];
  const bottleneckEdgeId = topBottleneck ? `${topBottleneck.from}->${topBottleneck.to}` : null;

  const detail = useMemo(() => {
    if (!selected || !process) return null;
    const node = process.nodes.find((n) => n.id === selected);
    if (!node) return null;
    // The actual cases (with their record data) that passed through this activity.
    const records = (cases?.cases ?? [])
      .filter((c) => c.path.includes(selected))
      .map((c) => ({
        case_id: c.case_id,
        label: caseLabel(c),
        outcome: c.outcome,
        step: c.steps.find((s) => s.activity === selected) ?? null,
      }));
    return {
      node,
      incoming: process.edges.filter((e) => e.target === selected),
      outgoing: process.edges.filter((e) => e.source === selected),
      variantCount: variants?.variants.filter((v) => v.path.includes(selected)).length ?? 0,
      isBottleneck:
        !!topBottleneck && (topBottleneck.from === selected || topBottleneck.to === selected),
      records,
    };
  }, [selected, process, variants, topBottleneck, cases]);

  return (
    <div className="space-y-5">
      {/* ── Summary chips + refresh ──────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Chip icon={<Route className="w-3.5 h-3.5" />} label="Cases" value={summary?.total_cases ?? 0} />
          <Chip icon={<Activity className="w-3.5 h-3.5" />} label="Activities" value={summary?.total_activities ?? 0} />
          <Chip icon={<GitBranch className="w-3.5 h-3.5" />} label="Variants" value={summary?.variant_count ?? 0} />
          <Chip icon={<CheckCircle2 className="w-3.5 h-3.5" />} label="Conformance" value={`${summary?.conformance_rate ?? 0}%`} />
          <Chip icon={<Timer className="w-3.5 h-3.5" />} label="Avg cycle" value={summary?.avg_cycle_time_human ?? "—"} />
        </div>
        <div className="flex items-center gap-2">
          <MapModeToggle mode={mapMode} onChange={setMapMode} minedCases={summary?.total_cases ?? 0} />
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-gray-300 hover:text-white bg-ocean-card border border-ocean-border/60 hover:border-ocean-accent/50 transition disabled:opacity-60"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
        </div>
      </div>

      {loading && !process ? (
        <div className="h-[560px] flex items-center justify-center text-gray-500">
          <Loader2 className="w-6 h-6 animate-spin text-ocean-accent" />
        </div>
      ) : showReference ? (
        <ReferenceMapView reference={reference} minedCases={summary?.total_cases ?? 0} />
      ) : empty ? (
        <div className="glass rounded-2xl border border-ocean-border/50 p-10 text-center">
          <Route className="w-10 h-10 text-ocean-accent/50 mx-auto mb-3" />
          <p className="text-white font-semibold">No process mined yet</p>
          <p className="text-gray-400 text-sm mt-1 max-w-md mx-auto">
            OpsMap discovers the crew-change process from the events workflows emit.
            Run a sign-off from the <span className="text-ocean-accent">Dashboard</span> — each
            completed workflow adds one case — then hit Refresh.
          </p>
          <button
            onClick={() => setMapMode("reference")}
            className="mt-4 inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm text-ocean-accent bg-ocean-accent/10 border border-ocean-accent/40 hover:bg-ocean-accent/20 transition"
          >
            <Compass className="w-4 h-4" /> View the designed flow
          </button>
        </div>
      ) : (
        <div className="flex flex-col xl:flex-row gap-4">
          {/* ── Process map ─────────────────────────────────────────────────── */}
          <div className="glass rounded-2xl border border-ocean-border/50 p-4 flex-1 min-w-0">
            <Legend />
            {process && (
              <OpsMapGraph
                nodes={process.nodes}
                edges={process.edges}
                height={560}
                bottleneckEdgeId={bottleneckEdgeId}
                selectedId={selected}
                onNodeClick={setSelected}
              />
            )}
          </div>

          {/* ── Side panels ─────────────────────────────────────────────────── */}
          <div className="w-full xl:w-[360px] shrink-0 space-y-4">
            {detail && (
              <ActivityDetailPanel detail={detail} onClose={() => setSelected(null)} />
            )}
            <CasesPanel cases={cases} />
            <VariantsPanel variants={variants} />
            <BottlenecksPanel bottlenecks={bottlenecks} />
            <ConformancePanel conformance={conformance} />
          </div>
        </div>
      )}
    </div>
  );
}

function activityColor(label: string, terminal: boolean): string {
  if (label === "Signed On") return "#10b981";
  if (label === "Sign-On Rejected") return "#f59e0b";
  if (label === "Workflow Failed") return "#ef4444";
  return terminal ? "#94a3b8" : "#3b82f6";
}

// ── Discovered ⇄ Reference toggle ──────────────────────────────────────────────────
function MapModeToggle({
  mode, onChange, minedCases,
}: { mode: MapMode; onChange: (m: MapMode) => void; minedCases: number }) {
  const opts: { key: MapMode; label: string; icon: React.ReactNode; title: string }[] = [
    { key: "discovered", label: "Discovered", icon: <Activity className="w-3.5 h-3.5" />, title: "The process mined from real workflow events" },
    { key: "reference", label: "Reference", icon: <Compass className="w-3.5 h-3.5" />, title: "The designed crew-change flow (always shown)" },
  ];
  return (
    <div className="flex items-center rounded-xl border border-ocean-border/60 bg-ocean-card p-0.5">
      {opts.map((o) => {
        const active = mode === o.key;
        return (
          <button
            key={o.key}
            onClick={() => onChange(o.key)}
            title={o.title}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
              active
                ? "bg-ocean-accent/20 text-white border border-ocean-accent/50"
                : "text-gray-400 hover:text-white border border-transparent"
            }`}
          >
            {o.icon}
            {o.label}
            {o.key === "discovered" && <span className="text-[10px] text-gray-500">({minedCases})</span>}
          </button>
        );
      })}
    </div>
  );
}

// ── Reference (designed) process map + side panels ────────────────────────────────
function ReferenceMapView({
  reference, minedCases,
}: { reference: OpsMapProcess | null; minedCases: number }) {
  if (!reference) {
    return (
      <div className="h-[560px] flex items-center justify-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin text-ocean-accent" />
      </div>
    );
  }
  return (
    <div className="flex flex-col xl:flex-row gap-4">
      <div className="glass rounded-2xl border border-ocean-border/50 p-4 flex-1 min-w-0">
        <ReferenceLegend />
        <OpsMapGraph nodes={reference.nodes} edges={reference.edges} height={560} variant="reference" />
      </div>

      <div className="w-full xl:w-[360px] shrink-0 space-y-4">
        <Card title="Reference model" icon={<Workflow className="w-3.5 h-3.5" />}>
          <p className="text-xs text-gray-400">
            The crew-change process <span className="text-white font-medium">as designed</span> — the
            normative flow every case is expected to follow, independent of mined data. It is the
            baseline the <span className="text-ocean-accent">Discovered</span> model is scored against
            for conformance.
          </p>
          <div className="mt-3 flex items-start gap-2 text-[11px] text-gray-400 bg-ocean-card/40 rounded-lg px-2.5 py-2 border border-ocean-border/30">
            {minedCases > 0 ? (
              <>
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span>
                  {minedCases} {minedCases === 1 ? "case" : "cases"} mined — switch to{" "}
                  <span className="text-ocean-accent">Discovered</span> to see how work actually flowed.
                </span>
              </>
            ) : (
              <>
                <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                <span>No cases mined yet — run a sign-off from the Dashboard to populate the discovered model.</span>
              </>
            )}
          </div>
        </Card>

        <Card title="Designed steps" icon={<Route className="w-3.5 h-3.5" />} count={reference.nodes.length}>
          <ul className="space-y-1.5">
            {reference.nodes.map((n) => {
              const accent = activityColor(n.label, n.terminal);
              return (
                <li
                  key={n.id}
                  className="flex items-center justify-between gap-2 text-xs bg-ocean-card/40 rounded-lg px-2.5 py-1.5 border border-ocean-border/30"
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <i className="w-2 h-2 rounded-full shrink-0" style={{ background: accent }} />
                    <span className="text-gray-200 truncate">{n.label}</span>
                  </span>
                  {n.actor && <span className="text-[10px] text-gray-500 shrink-0">{n.actor}</span>}
                </li>
              );
            })}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function ReferenceLegend() {
  const items: [string, string, boolean][] = [
    ["happy path", "#3b6aa0", false],
    ["parallel", "#8b5cf6", true],
    ["rejected", "#f59e0b", false],
    ["failed", "#ef4444", false],
  ];
  return (
    <div className="flex flex-wrap items-center gap-3 mb-3 text-[11px] text-gray-400">
      {items.map(([label, color, dashed]) => (
        <span key={label} className="flex items-center gap-1.5">
          <i
            className="w-4 h-0.5"
            style={{
              background: dashed
                ? `repeating-linear-gradient(90deg, ${color} 0 4px, transparent 4px 7px)`
                : color,
            }}
          />
          {label}
        </span>
      ))}
      <span className="ml-auto text-gray-600">designed flow · drag nodes · scroll to zoom</span>
    </div>
  );
}

interface ActivityRecord {
  case_id: string;
  label: string;
  outcome: OpsMapOutcome;
  step: OpsMapCaseStep | null;
}
interface ActivityDetail {
  node: { id: string; label: string; cases: number; terminal: boolean };
  incoming: { id: string; source: string; target: string; count: number; avg_seconds: number; label: string }[];
  outgoing: { id: string; source: string; target: string; count: number; avg_seconds: number; label: string }[];
  variantCount: number;
  isBottleneck: boolean;
  records: ActivityRecord[];
}

// Human labels for the curated record-detail keys shown per case.
const DETAIL_LABEL: Record<string, string> = {
  crew_name: "Crew", rank: "Rank", vessel: "Vessel", crew_id: "Crew ID",
  candidate_name: "Candidate", candidate_rank: "Rank", crew_rank: "Rank",
  compliance_status: "Status", compliance_score: "Score", status: "Status",
  pool: "Pool", error: "Error", failures: "Failures", recommendation: "Note",
  message: "Detail",
};
const DETAIL_ORDER = [
  "crew_name", "candidate_name", "crew_rank", "candidate_rank", "rank", "vessel",
  "compliance_status", "compliance_score", "failures", "error", "recommendation", "message",
];

function formatDetailValue(key: string, value: string | number | string[]): string {
  if (Array.isArray(value)) return value.map(String).join("; ");
  if (key === "compliance_score") return `${value}%`;
  return String(value);
}

function ActivityDetailPanel({ detail, onClose }: { detail: ActivityDetail; onClose: () => void }) {
  const { node, incoming, outgoing, variantCount, isBottleneck, records } = detail;
  const accent = activityColor(node.label, node.terminal);
  const meta = ACTIVITY_META[node.label];

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4" style={{ borderColor: `${accent}55` }}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <span
            className="inline-block px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider mb-1"
            style={{ color: accent, background: `${accent}1a`, border: `1px solid ${accent}55` }}
          >
            ACTIVITY{node.terminal ? " · TERMINAL" : ""}
          </span>
          <h3 className="text-base font-bold text-white leading-tight">{node.label}</h3>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-white p-1" aria-label="Close">
          <X className="w-4 h-4" />
        </button>
      </div>

      {meta && <p className="text-xs text-gray-400 mb-3">{meta.about}</p>}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <Stat label="Cases" value={String(node.cases)} />
        <Stat label="In variants" value={String(variantCount)} />
      </div>

      {/* Source event + actor */}
      {meta && (
        <div className="space-y-1.5 mb-3 text-xs">
          <Row icon={<Zap className="w-3 h-3 text-ocean-accent" />} label="Mined from" value={meta.event} mono />
          <Row icon={<User className="w-3 h-3 text-ocean-accent" />} label="Actor" value={meta.actor} />
        </div>
      )}

      {isBottleneck && (
        <div className="flex items-center gap-1.5 text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-2.5 py-1.5 mb-3">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          On the slowest handoff (current bottleneck).
        </div>
      )}

      {/* Records — the actual cases that hit this activity, with their data. */}
      <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5">
        Records ({records.length})
      </p>
      <ul className="space-y-1.5 mb-3 max-h-[260px] overflow-y-auto pr-1">
        {records.map((r) => {
          const o = OUTCOME_STYLE[r.outcome];
          const details = r.step?.details ?? {};
          const keys = DETAIL_ORDER.filter((k) => k in details);
          return (
            <li key={r.case_id} className="bg-ocean-card/40 rounded-lg p-2 border border-ocean-border/30">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-xs text-gray-200 font-medium truncate">{r.label}</span>
                <span
                  className="px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0"
                  style={{ color: o.color, background: `${o.color}1a`, border: `1px solid ${o.color}55` }}
                >
                  {o.label}
                </span>
              </div>
              {keys.length > 0 ? (
                <div className="space-y-0.5">
                  {keys.map((k) => (
                    <div key={k} className="flex items-start justify-between gap-3 text-[11px]">
                      <span className="text-gray-500 shrink-0">{DETAIL_LABEL[k] ?? k}</span>
                      <span className="text-gray-300 text-right break-words">{formatDetailValue(k, details[k])}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-gray-600">No recorded details for this step.</p>
              )}
            </li>
          );
        })}
        {records.length === 0 && (
          <li className="text-xs text-gray-600">No cases hit this activity yet.</li>
        )}
      </ul>

      {/* Transitions */}
      <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5">
        Transitions ({incoming.length + outgoing.length})
      </p>
      <ul className="space-y-1 max-h-[180px] overflow-y-auto pr-1">
        {incoming.map((e) => (
          <li key={e.id} className="flex items-center gap-2 text-xs bg-ocean-card/40 rounded-lg px-2 py-1.5 border border-ocean-border/30">
            <ArrowLeft className="w-3 h-3 text-amber-400 shrink-0" />
            <span className="text-gray-300 truncate flex-1">{e.source}</span>
            <span className="text-gray-500 shrink-0">{e.count}× · {fmt(e.avg_seconds)}</span>
          </li>
        ))}
        {outgoing.map((e) => (
          <li key={e.id} className="flex items-center gap-2 text-xs bg-ocean-card/40 rounded-lg px-2 py-1.5 border border-ocean-border/30">
            <ArrowRight className="w-3 h-3 text-ocean-accent shrink-0" />
            <span className="text-gray-300 truncate flex-1">{e.target}</span>
            <span className="text-gray-500 shrink-0">{e.count}× · {fmt(e.avg_seconds)}</span>
          </li>
        ))}
        {incoming.length + outgoing.length === 0 && (
          <li className="text-xs text-gray-600">No transitions recorded.</li>
        )}
      </ul>
    </div>
  );
}

// Compact "who → whom" label for a case (falls back to the short case id).
function caseLabel(c: OpsMapCase): string {
  const off = c.sign_off_crew;
  const on = c.sign_on_crew;
  if (off && on) return `${off} → ${on}`;
  if (off) return `${off} → —`;
  if (on) return `— → ${on}`;
  return c.case_id.slice(0, 8);
}

function CasesPanel({ cases }: { cases: OpsMapCases | null }) {
  return (
    <Card title="Cases" icon={<Users className="w-3.5 h-3.5" />} count={cases?.total_cases}>
      {!cases || cases.cases.length === 0 ? (
        <Empty text="No cases yet." />
      ) : (
        <ul className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
          {cases.cases.map((c) => {
            const o = OUTCOME_STYLE[c.outcome];
            return (
              <li key={c.case_id} className="bg-ocean-card/40 rounded-lg p-2.5 border border-ocean-border/30">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="flex items-center gap-1.5 text-xs text-white font-medium min-w-0">
                    <Ship className="w-3 h-3 text-ocean-accent shrink-0" />
                    <span className="truncate">{caseLabel(c)}</span>
                  </span>
                  <span
                    className="px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0"
                    style={{ color: o.color, background: `${o.color}1a`, border: `1px solid ${o.color}55` }}
                  >
                    {o.label}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
                  {c.sign_off_vessel && <span>{c.sign_off_vessel}</span>}
                  {c.compliance_score !== null && (
                    <span>Score <span className="text-gray-300">{c.compliance_score}%</span></span>
                  )}
                  <span>{c.cycle_time_human}</span>
                </div>
                {c.reason && (
                  <p className="text-[11px] text-amber-300/80 mt-1 break-words">{c.reason}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-ocean-card/40 rounded-lg px-2.5 py-1.5 border border-ocean-border/30">
      <div className="text-lg font-bold text-white leading-none">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-gray-500 mt-1">{label}</div>
    </div>
  );
}

function Row({ icon, label, value, mono }: { icon: React.ReactNode; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-1.5 text-gray-500 shrink-0">{icon}{label}</span>
      <span className={`text-gray-200 text-right break-words ${mono ? "font-mono text-[11px]" : ""}`}>{value}</span>
    </div>
  );
}

// Compact human duration (seconds → "40s" / "1.3m" / "1.1h").
function fmt(seconds: number): string {
  const s = Math.max(0, seconds || 0);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function VariantsPanel({ variants }: { variants: OpsMapVariants | null }) {
  return (
    <Card title="Variants" icon={<GitBranch className="w-3.5 h-3.5" />} count={variants?.variant_count}>
      {!variants || variants.variants.length === 0 ? (
        <Empty text="No variants yet." />
      ) : (
        <ul className="space-y-2.5 max-h-[260px] overflow-y-auto pr-1">
          {variants.variants.map((v) => {
            const o = OUTCOME_STYLE[v.outcome];
            return (
              <li key={v.id} className="bg-ocean-card/40 rounded-lg p-2.5 border border-ocean-border/30">
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span
                    className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                    style={{ color: o.color, background: `${o.color}1a`, border: `1px solid ${o.color}55` }}
                  >
                    {o.label}
                  </span>
                  <span className="text-[11px] text-gray-400">
                    {v.case_count} {v.case_count === 1 ? "case" : "cases"} · {v.percentage}% · {v.avg_cycle_time_human}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  {v.path.map((step, i) => (
                    <span key={i} className="flex items-center gap-1">
                      <span className="text-[10px] text-gray-300 bg-ocean-border/30 rounded px-1.5 py-0.5">{step}</span>
                      {i < v.path.length - 1 && <span className="text-gray-600 text-[10px]">→</span>}
                    </span>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function BottlenecksPanel({ bottlenecks }: { bottlenecks: OpsMapBottlenecks | null }) {
  return (
    <Card title="Bottlenecks" icon={<Timer className="w-3.5 h-3.5" />} count={bottlenecks?.bottlenecks.length}>
      {!bottlenecks || bottlenecks.bottlenecks.length === 0 ? (
        <Empty text="No handoffs yet." />
      ) : (
        <ul className="space-y-1.5">
          {bottlenecks.bottlenecks.map((b, i) => (
            <li key={i} className="flex items-center justify-between gap-2 text-xs bg-ocean-card/40 rounded-lg px-2.5 py-1.5 border border-ocean-border/30">
              <span className="flex items-center gap-1 min-w-0">
                <span className="text-gray-300 truncate">{b.from}</span>
                <span className="text-gray-600">→</span>
                <span className="text-gray-300 truncate">{b.to}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className={i === 0 ? "text-red-400 font-semibold" : "text-amber-300"}>{b.avg_human}</span>
                <span className="text-gray-600"> · {b.occurrences}×</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ConformancePanel({ conformance }: { conformance: OpsMapConformance | null }) {
  const rate = conformance?.conformance_rate ?? 0;
  const rateColor = rate >= 80 ? "#10b981" : rate >= 50 ? "#f59e0b" : "#ef4444";
  return (
    <Card title="Conformance" icon={<CheckCircle2 className="w-3.5 h-3.5" />}>
      {!conformance ? (
        <Empty text="No data yet." />
      ) : (
        <>
          <div className="flex items-center gap-3 mb-3">
            <div className="text-2xl font-bold" style={{ color: rateColor }}>{rate}%</div>
            <div className="text-xs text-gray-400">
              {conformance.conformant_cases} of {conformance.total_cases} cases match the happy path
            </div>
          </div>
          <div className="w-full h-1.5 rounded-full bg-ocean-border/40 overflow-hidden mb-3">
            <div className="h-full rounded-full" style={{ width: `${rate}%`, background: rateColor }} />
          </div>
          {conformance.deviations.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5">Deviations</p>
              <ul className="space-y-1.5 max-h-[180px] overflow-y-auto pr-1">
                {conformance.deviations.map((d) => (
                  <li key={d.case_id} className="flex items-start gap-2 text-xs bg-ocean-card/40 rounded-lg px-2.5 py-1.5 border border-ocean-border/30">
                    {d.path[d.path.length - 1] === "Workflow Failed"
                      ? <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
                      : <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />}
                    <span className="text-gray-300">{d.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function Card({
  title, icon, count, children,
}: {
  title: string; icon: React.ReactNode; count?: number; children: React.ReactNode;
}) {
  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-3">
        <span className="text-ocean-accent">{icon}</span>
        {title}
        {count !== undefined && <span className="text-xs text-gray-500 font-normal">({count})</span>}
      </h3>
      {children}
    </div>
  );
}

function Chip({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg glass border border-ocean-border/40 text-xs">
      <span className="text-ocean-accent">{icon}</span>
      <span className="text-gray-400">{label}</span>
      <span className="text-white font-semibold">{value}</span>
    </span>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-xs text-gray-600">{text}</p>;
}

function Legend() {
  const items: [string, string][] = [
    ["Step", "#3b82f6"],
    ["Signed On", "#10b981"],
    ["Rejected", "#f59e0b"],
    ["Failed", "#ef4444"],
  ];
  return (
    <div className="flex flex-wrap items-center gap-3 mb-3 text-[11px] text-gray-400">
      {items.map(([label, color]) => (
        <span key={label} className="flex items-center gap-1.5">
          <i className="w-2.5 h-2.5 rounded-full" style={{ background: color }} /> {label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <i className="w-4 h-0.5" style={{ background: "#ef4444" }} /> bottleneck
      </span>
      <span className="ml-auto text-gray-600">drag nodes · scroll to zoom</span>
    </div>
  );
}
