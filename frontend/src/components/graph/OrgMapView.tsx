"use client";
/**
 * OrgMapView — the L2 OrgMap (organizational hierarchy) dimension. Renders the
 * Company → Fleet → Vessel structure (overlaid on EntityMap vessels) alongside the
 * headline manning-gap query: required vs. have headcount per rank, scoped to the
 * whole org, a company, or a fleet. Clicking a Company/Fleet node sets the scope.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import {
  Building2, Layers, Ship, Users, RefreshCw, Loader2, Share2, AlertTriangle, CheckCircle2, X,
} from "lucide-react";

import {
  orgMapApi,
  type OrgMapSummary, type OrgMapStructure, type OrgMapManningGap,
} from "@/lib/api";
import OrgMapGraph, { ORG_TYPE_COLOR, type OrgGraphNode } from "./OrgMapGraph";

// Scope key encodes the filter: "" = whole org, "company:X", "fleet:X", "vessel:X".
function scopeToParams(key: string): { company?: string; fleet?: string; vessel?: string } {
  if (key.startsWith("company:")) return { company: key.slice(8) };
  if (key.startsWith("fleet:")) return { fleet: key.slice(6) };
  if (key.startsWith("vessel:")) return { vessel: key.slice(7) };
  return {};
}
// Map a scope key to the structure-graph node id, so the graph highlights the scope.
function scopeToNodeId(key: string): string | null {
  if (key.startsWith("company:")) return `co:${key.slice(8)}`;
  if (key.startsWith("fleet:")) return `f:${key.slice(6)}`;
  if (key.startsWith("vessel:")) return `v:${key.slice(7)}`;
  return null;
}

// Filter the full Company→Fleet→Vessel structure down to the subtree connected to a
// selected node — its descendants (fleets/vessels) plus its ancestors (parent company),
// so a fleet/company selection redraws as an independent, focused graph. Edges are kept
// only when both endpoints survive. No selection ("" scope) returns the structure as-is.
function filterStructure(
  structure: OrgMapStructure,
  scopeKey: string,
): { nodes: OrgGraphNode[]; edges: OrgMapStructure["edges"] } {
  const rootId = scopeToNodeId(scopeKey);
  if (!rootId) return { nodes: structure.nodes, edges: structure.edges };

  const forward = new Map<string, string[]>();   // source → targets (down the hierarchy)
  const backward = new Map<string, string[]>();   // target → sources (up the hierarchy)
  for (const e of structure.edges) {
    (forward.get(e.source) ?? forward.set(e.source, []).get(e.source)!).push(e.target);
    (backward.get(e.target) ?? backward.set(e.target, []).get(e.target)!).push(e.source);
  }

  const keep = new Set<string>();
  const walk = (start: string, adj: Map<string, string[]>) => {
    const stack = [start];
    while (stack.length) {
      const id = stack.pop()!;
      if (keep.has(id)) continue;
      keep.add(id);
      for (const next of adj.get(id) ?? []) stack.push(next);
    }
  };
  walk(rootId, forward);   // descendants
  walk(rootId, backward);  // ancestors

  return {
    nodes: structure.nodes.filter((n) => keep.has(n.id)),
    edges: structure.edges.filter((e) => keep.has(e.source) && keep.has(e.target)),
  };
}

export default function OrgMapView() {
  const [summary, setSummary] = useState<OrgMapSummary | null>(null);
  const [structure, setStructure] = useState<OrgMapStructure | null>(null);
  const [manning, setManning] = useState<OrgMapManningGap | null>(null);
  const [scopeKey, setScopeKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  const loadBase = useCallback(async () => {
    setLoading(true);
    try {
      const [s, st] = await Promise.all([orgMapApi.getSummary(), orgMapApi.getStructure()]);
      setSummary(s); setStructure(st);
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 503) setUnavailable(true);
      else toast.error("Failed to load OrgMap");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadManning = useCallback(async (key: string) => {
    try {
      setManning(await orgMapApi.getManningGap(scopeToParams(key)));
    } catch {
      /* manning is secondary; ignore transient errors */
    }
  }, []);

  useEffect(() => { loadBase(); }, [loadBase]);
  useEffect(() => { loadManning(scopeKey); }, [scopeKey, loadManning]);

  const selectedId = useMemo(() => scopeToNodeId(scopeKey), [scopeKey]);

  // The structure redrawn for the current scope: whole org, or just the selected
  // company/fleet/vessel subtree (an independent graph). Recomputed client-side, no
  // refetch. When a vessel is scoped, its role layer (Rank nodes from the manning data)
  // is appended as a 4th column so the per-vessel role hierarchy is visible inline.
  const view = useMemo(() => {
    if (!structure) return null;
    const base = filterStructure(structure, scopeKey);
    const { vessel } = scopeToParams(scopeKey);
    if (!vessel || !manning || manning.scope.vessel !== vessel) return base;

    const vId = `v:${vessel}`;
    const rankNodes: OrgGraphNode[] = manning.rows.map((r) => ({
      id: `r:${r.rank}`, type: "Rank", label: r.rank,
      sublabel: `${r.have}/${r.required}`, short: r.gap > 0,
    }));
    const rankEdges = manning.rows.map((r) => ({
      id: `${vId}->r:${r.rank}`, source: vId, target: `r:${r.rank}`,
      label: `×${r.required}`,
    }));
    return { nodes: [...base.nodes, ...rankNodes], edges: [...base.edges, ...rankEdges] };
  }, [structure, scopeKey, manning]);

  // Vessel names (for the scope dropdown) — pulled from the structure graph.
  const vesselNames = useMemo(
    () => (structure?.nodes ?? []).filter((n) => n.type === "Vessel").map((n) => n.label),
    [structure],
  );

  // Human-readable label for the active filter banner.
  const scopeLabel = useMemo(() => {
    const p = scopeToParams(scopeKey);
    if (p.company) return { kind: "Company", name: p.company };
    if (p.fleet) return { kind: "Fleet", name: p.fleet };
    if (p.vessel) return { kind: "Vessel", name: p.vessel };
    return null;
  }, [scopeKey]);

  // Clicking a node redraws the graph to that subtree + scopes the manning gap.
  // Company/Fleet drill down the ownership tree; a Vessel reveals its role/rank layer.
  const handleNodeClick = useCallback((id: string) => {
    if (id.startsWith("co:")) setScopeKey(`company:${id.slice(3)}`);
    else if (id.startsWith("f:")) setScopeKey(`fleet:${id.slice(2)}`);
    else if (id.startsWith("v:")) setScopeKey(`vessel:${id.slice(2)}`);
  }, []);

  if (unavailable) {
    return (
      <div className="glass rounded-2xl border border-amber-500/30 p-8 text-center">
        <p className="text-amber-300 font-semibold">Graph backend disabled</p>
        <p className="text-gray-400 text-sm mt-1">
          Set <code className="text-ocean-accent">GRAPH_BACKEND=age</code> and seed OrgMap
          (<code className="text-ocean-accent">python -m L2Knowledge_graph.scripts.seed_org_map</code>) to enable this view.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* ── Summary chips + refresh ──────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Chip icon={<Building2 className="w-3.5 h-3.5" />} label="Companies" value={summary?.nodes["Company"] ?? 0} color={ORG_TYPE_COLOR.Company} />
          <Chip icon={<Layers className="w-3.5 h-3.5" />} label="Fleets" value={summary?.nodes["Fleet"] ?? 0} color={ORG_TYPE_COLOR.Fleet} />
          <Chip icon={<Ship className="w-3.5 h-3.5" />} label="Vessels" value={summary?.nodes["Vessel"] ?? 0} color={ORG_TYPE_COLOR.Vessel} />
          <Chip icon={<Users className="w-3.5 h-3.5" />} label="Ranks" value={summary?.nodes["Rank"] ?? 0} color="#94a3b8" />
          <span className="px-2.5 py-1 rounded-lg glass border border-ocean-border/40 text-xs text-gray-400">
            {summary?.total_nodes ?? 0} nodes · {summary?.total_edges ?? 0} edges
          </span>
        </div>
        <button
          onClick={() => { loadBase(); loadManning(scopeKey); }}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-gray-300 hover:text-white bg-ocean-card border border-ocean-border/60 hover:border-ocean-accent/50 transition disabled:opacity-60"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Refresh
        </button>
      </div>

      {loading && !structure ? (
        <div className="h-[560px] flex items-center justify-center text-gray-500">
          <Loader2 className="w-6 h-6 animate-spin text-ocean-accent" />
        </div>
      ) : (
        <div className="flex flex-col xl:flex-row gap-4">
          {/* ── Structure graph ─────────────────────────────────────────────── */}
          <div className="glass rounded-2xl border border-ocean-border/50 p-4 flex-1 min-w-0">
            <Legend />
            {scopeLabel && (
              <div className="flex items-center gap-2 mb-3 text-xs">
                <span className="text-gray-500">Filtered to</span>
                <span
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border font-medium"
                  style={{
                    borderColor: `${ORG_TYPE_COLOR[scopeLabel.kind]}66`,
                    color: ORG_TYPE_COLOR[scopeLabel.kind],
                  }}
                >
                  {scopeLabel.kind === "Company" ? <Building2 className="w-3 h-3" />
                    : scopeLabel.kind === "Fleet" ? <Layers className="w-3 h-3" />
                    : <Ship className="w-3 h-3" />}
                  {scopeLabel.kind} · {scopeLabel.name}
                </span>
                <button
                  onClick={() => setScopeKey("")}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-gray-400 hover:text-white bg-ocean-card border border-ocean-border/60 hover:border-ocean-accent/50 transition"
                >
                  <X className="w-3 h-3" /> Show all
                </button>
              </div>
            )}
            {view && (
              <OrgMapGraph
                key={scopeKey || "all"}
                nodes={view.nodes}
                edges={view.edges}
                height={560}
                selectedId={selectedId}
                onNodeClick={handleNodeClick}
              />
            )}
          </div>

          {/* ── Manning-gap panel ───────────────────────────────────────────── */}
          <div className="w-full xl:w-[400px] shrink-0">
            <ManningPanel
              summary={summary}
              vessels={vesselNames}
              manning={manning}
              scopeKey={scopeKey}
              onScopeChange={setScopeKey}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ManningPanel({
  summary, vessels, manning, scopeKey, onScopeChange,
}: {
  summary: OrgMapSummary | null;
  vessels: string[];
  manning: OrgMapManningGap | null;
  scopeKey: string;
  onScopeChange: (k: string) => void;
}) {
  const totals = manning?.totals;
  const gapColor = (totals?.gap ?? 0) > 0 ? "#ef4444" : "#10b981";

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-3">
        <Users className="w-3.5 h-3.5 text-ocean-accent" /> Manning gap
      </h3>

      {/* Scope selector */}
      <select
        value={scopeKey}
        onChange={(e) => onScopeChange(e.target.value)}
        className="w-full bg-ocean-card border border-ocean-border/60 rounded-xl px-3 py-2 text-sm text-white mb-3 focus:outline-none focus:border-ocean-accent/60"
      >
        <option value="">Whole organization</option>
        {summary?.companies.map((c) => (
          <option key={`company:${c}`} value={`company:${c}`}>Company · {c}</option>
        ))}
        {summary?.fleets.map((f) => (
          <option key={`fleet:${f}`} value={`fleet:${f}`}>Fleet · {f}</option>
        ))}
        {vessels.map((v) => (
          <option key={`vessel:${v}`} value={`vessel:${v}`}>Vessel · {v}</option>
        ))}
      </select>

      {/* Totals */}
      {totals && (
        <div className="grid grid-cols-3 gap-2 mb-3">
          <Tile label="Required" value={totals.required} />
          <Tile label="Have" value={totals.have} />
          <Tile label="Gap" value={totals.gap} color={gapColor} />
        </div>
      )}

      {/* Per-rank table */}
      {!manning || manning.rows.length === 0 ? (
        <p className="text-xs text-gray-600">No manning data.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-ocean-border/30">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-ocean-card/60 text-gray-400">
                <th className="text-left font-medium px-2.5 py-1.5">Rank</th>
                <th className="text-right font-medium px-2 py-1.5">Req</th>
                <th className="text-right font-medium px-2 py-1.5">Have</th>
                <th className="text-right font-medium px-2.5 py-1.5">Gap</th>
              </tr>
            </thead>
            <tbody>
              {manning.rows.map((r) => {
                const short = r.gap > 0;
                const over = r.gap < 0;
                return (
                  <tr key={r.rank} className="border-t border-ocean-border/20">
                    <td className="px-2.5 py-1.5 text-gray-200">{r.rank}</td>
                    <td className="px-2 py-1.5 text-right text-gray-400">{r.required}</td>
                    <td className="px-2 py-1.5 text-right text-gray-400">{r.have}</td>
                    <td className="px-2.5 py-1.5 text-right font-semibold">
                      <span className={short ? "text-red-400" : over ? "text-amber-300" : "text-emerald-400"}>
                        {short ? `+${r.gap}` : r.gap}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-gray-500 mt-2 flex items-center gap-1.5">
        <AlertTriangle className="w-3 h-3 text-red-400" /> positive gap = short-staffed ·
        <CheckCircle2 className="w-3 h-3 text-emerald-400" /> 0 = fully manned
      </p>
    </div>
  );
}

function Tile({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="bg-ocean-card/40 rounded-lg px-2.5 py-2 border border-ocean-border/30 text-center">
      <div className="text-lg font-bold leading-none" style={{ color: color || "#fff" }}>{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-gray-500 mt-1">{label}</div>
    </div>
  );
}

function Chip({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  return (
    <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg glass border border-ocean-border/40 text-xs">
      <i className="w-2 h-2 rounded-full" style={{ background: color }} />
      <span className="text-ocean-accent">{icon}</span>
      <span className="text-gray-400">{label}</span>
      <span className="text-white font-semibold">{value}</span>
    </span>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-3 text-[11px] text-gray-400">
      {Object.entries(ORG_TYPE_COLOR).map(([type, color]) => (
        <span key={type} className="flex items-center gap-1.5">
          <i className="w-2.5 h-2.5 rounded-full" style={{ background: color }} /> {type}
        </span>
      ))}
      <span className="ml-auto flex items-center gap-1.5 text-gray-600">
        <Share2 className="w-3 h-3" /> click a Company/Fleet to filter · click a Vessel to reveal its roles
      </span>
    </div>
  );
}
