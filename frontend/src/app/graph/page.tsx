"use client";
/**
 * Knowledge Graph — the L2 EntityMap "Standalone Query UI" (per the L2 plan).
 * Pick any combination of rank / certificate / port and see the matching crew and
 * their relationships rendered live from the Apache AGE graph (GET /graph/subgraph).
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import toast, { Toaster } from "react-hot-toast";
import {
  Anchor, Ship, Activity, BarChart3, Share2, Search, X, Loader2, Database,
  ArrowRight, ArrowLeft, GitBranch,
} from "lucide-react";

import {
  graphApi, type GraphFacets, type GraphSummary, type GraphSubgraph, type GraphNodeDetail,
} from "@/lib/api";
import EntityGraph, { TYPE_COLOR } from "@/components/graph/EntityGraph";

export default function GraphPage() {
  const [facets, setFacets] = useState<GraphFacets | null>(null);
  const [summary, setSummary] = useState<GraphSummary | null>(null);
  const [rank, setRank] = useState("");
  const [certificate, setCertificate] = useState("");
  const [port, setPort] = useState("");
  const [data, setData] = useState<GraphSubgraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const runSearch = useCallback(async (r: string, c: string, p: string) => {
    setLoading(true);
    setSelectedId(null);
    setDetail(null);
    try {
      const res = await graphApi.getSubgraph({
        rank: r || undefined,
        certificate: c || undefined,
        port: p || undefined,
        limit: 14,
      });
      setData(res);
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 503) setUnavailable(true);
      else toast.error("Graph query failed");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleNodeClick = useCallback(async (id: string) => {
    setSelectedId(id);
    setDetailLoading(true);
    try {
      setDetail(await graphApi.getNode(id));
    } catch {
      toast.error("Could not load node details");
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // Initial load: facets, summary, and a first (unfiltered) subgraph.
  useEffect(() => {
    (async () => {
      try {
        const [f, s] = await Promise.all([graphApi.getFacets(), graphApi.getSummary()]);
        setFacets(f);
        setSummary(s);
        runSearch("", "", "");
      } catch (err: unknown) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 503) setUnavailable(true);
        else toast.error("Failed to load graph");
      }
    })();
  }, [runSearch]);

  const clear = () => {
    setRank(""); setCertificate(""); setPort("");
    runSearch("", "", "");
  };

  const hasFilters = rank || certificate || port;

  return (
    <div className="min-h-screen bg-ocean-gradient">
      <Toaster position="top-right" toastOptions={{
        style: { background: "#0d1f3c", color: "#e2e8f0", border: "1px solid #1e3a5f" },
      }} />

      {/* ── Nav ─────────────────────────────────────────────────────────────── */}
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
            <NavLink href="/graph" icon={<Share2 className="w-4 h-4" />} label="Graph" active />
            <NavLink href="/monitoring" icon={<BarChart3 className="w-4 h-4" />} label="Monitoring" />
            <NavLink href="/decisions" icon={<GitBranch className="w-4 h-4" />} label="Decisions" />
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Database className="w-3.5 h-3.5 text-ocean-accent" />
            <span>L2 EntityMap · AGE</span>
          </div>
        </div>
      </nav>

      <div className="max-w-screen-2xl mx-auto px-6 py-6 space-y-5">
        {/* ── Header + summary chips ───────────────────────────────────────── */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Share2 className="w-5 h-5 text-ocean-accent" /> L2 Knowledge Graph
            </h2>
            <p className="text-sm text-gray-500">
              EntityMap — search crew by rank, certificate &amp; port across the maritime context graph.
            </p>
          </div>
          {summary && (
            <div className="flex flex-wrap items-center gap-2">
              {summary.labels.map((l) => (
                <span key={l} className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg glass border border-ocean-border/40 text-xs">
                  <i className="w-2 h-2 rounded-full" style={{ background: TYPE_COLOR[l] || "#94a3b8" }} />
                  <span className="text-gray-400">{l}</span>
                  <span className="text-white font-semibold">{summary.nodes[l] ?? 0}</span>
                </span>
              ))}
              <span className="px-2.5 py-1 rounded-lg glass border border-ocean-border/40 text-xs text-gray-400">
                {summary.total_nodes} nodes · {summary.total_edges} edges
              </span>
            </div>
          )}
        </div>

        {unavailable ? (
          <div className="glass rounded-2xl border border-amber-500/30 p-8 text-center">
            <p className="text-amber-300 font-semibold">Graph backend disabled</p>
            <p className="text-gray-400 text-sm mt-1">
              Set <code className="text-ocean-accent">GRAPH_BACKEND=age</code> and seed the graph
              (<code className="text-ocean-accent">python -m scripts.seed_entity_map</code>) to enable this view.
            </p>
          </div>
        ) : (
          <>
            {/* ── Filter bar ─────────────────────────────────────────────────── */}
            <div className="glass rounded-2xl border border-ocean-border/50 p-4">
              <div className="flex flex-wrap items-end gap-3">
                <Select label="Rank" value={rank} onChange={setRank} options={facets?.ranks ?? []} />
                <Select label="Certificate" value={certificate} onChange={setCertificate} options={facets?.certificates ?? []} />
                <Select label="Port" value={port} onChange={setPort} options={facets?.ports ?? []} />
                <button
                  onClick={() => runSearch(rank, certificate, port)}
                  disabled={loading}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent-gradient text-white text-sm font-medium shadow-lg disabled:opacity-60"
                >
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  Search
                </button>
                {hasFilters && (
                  <button
                    onClick={clear}
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-sm text-gray-400 hover:text-white hover:bg-ocean-border/30 transition"
                  >
                    <X className="w-4 h-4" /> Clear
                  </button>
                )}
                {data && (
                  <span className="ml-auto text-xs text-gray-500">
                    <span className="text-white font-semibold">{data.crew_count}</span> crew ·{" "}
                    {data.total_nodes} nodes · {data.total_edges} edges ·{" "}
                    <span className="text-teal-300">{data.elapsed_ms} ms</span>
                  </span>
                )}
              </div>
            </div>

            {/* ── Graph + node details ───────────────────────────────────────── */}
            <div className="flex flex-col xl:flex-row gap-4">
              <div className="glass rounded-2xl border border-ocean-border/50 p-4 flex-1 min-w-0">
                <Legend />
                {data && data.nodes.length > 0 ? (
                  <EntityGraph
                    nodes={data.nodes}
                    edges={data.edges}
                    height={560}
                    selectedId={selectedId}
                    onNodeClick={handleNodeClick}
                  />
                ) : (
                  <div className="h-[560px] flex flex-col items-center justify-center text-gray-500 gap-2">
                    {loading ? (
                      <Loader2 className="w-6 h-6 animate-spin text-ocean-accent" />
                    ) : (
                      <>
                        <Share2 className="w-8 h-8 opacity-40" />
                        <p>No crew match these filters.</p>
                      </>
                    )}
                  </div>
                )}
              </div>

              {(selectedId || detailLoading) && (
                <NodeDetailPanel
                  detail={detail}
                  loading={detailLoading}
                  onClose={() => { setSelectedId(null); setDetail(null); }}
                  onJump={handleNodeClick}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// Order/format crew properties nicely; hide empty values.
const PROP_LABELS: Record<string, string> = {
  crew_id: "Crew ID", name: "Name", rank: "Rank", grade: "Grade",
  nationality: "Nationality", port: "Port", vessel: "Vessel", status: "Status",
  pool: "Pool", experience_years: "Experience (yrs)", type: "Type",
  contract_id: "Contract ID", start_date: "Start date",
};

function NodeDetailPanel({
  detail, loading, onClose, onJump,
}: {
  detail: GraphNodeDetail | null; loading: boolean; onClose: () => void;
  onJump: (id: string) => void;
}) {
  const accent = detail ? (TYPE_COLOR[detail.label] || "#94a3b8") : "#94a3b8";
  const props = detail
    ? Object.entries(detail.properties).filter(([, v]) => v !== null && v !== "")
    : [];
  const title = detail
    ? String(detail.properties.name ?? detail.properties.type ??
        detail.properties.contract_id ?? detail.properties.crew_id ?? detail.label)
    : "";

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4 w-full xl:w-[340px] shrink-0">
      {loading || !detail ? (
        <div className="h-full min-h-[200px] flex items-center justify-center text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin text-ocean-accent" />
        </div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-2 mb-3">
            <div>
              <span
                className="inline-block px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider mb-1"
                style={{ color: accent, background: `${accent}1a`, border: `1px solid ${accent}55` }}
              >
                {detail.label.toUpperCase()}
              </span>
              <h3 className="text-base font-bold text-white leading-tight">{title}</h3>
            </div>
            <button onClick={onClose} className="text-gray-500 hover:text-white p-1" aria-label="Close">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Properties */}
          <div className="space-y-1.5 mb-4">
            {props.map(([k, v]) => (
              <div key={k} className="flex items-start justify-between gap-3 text-xs">
                <span className="text-gray-500 shrink-0">{PROP_LABELS[k] || k}</span>
                <span className="text-gray-200 text-right break-words">{String(v)}</span>
              </div>
            ))}
          </div>

          {/* Relationships */}
          <div>
            <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5 flex items-center gap-1.5">
              <Share2 className="w-3 h-3" /> Relationships ({detail.degree})
            </p>
            <ul className="space-y-1 max-h-[280px] overflow-y-auto pr-1">
              {detail.relationships.map((r, i) => {
                const otherAccent = TYPE_COLOR[r.other_type] || "#94a3b8";
                return (
                  <li key={i}>
                    <button
                      onClick={() => onJump(String(r.other_id))}
                      title={`Jump to ${r.other} (${r.other_type})`}
                      className="w-full flex items-center gap-2 text-xs bg-ocean-card/40 hover:bg-ocean-accent/15 border border-transparent hover:border-ocean-accent/30 rounded-lg px-2 py-1.5 transition-colors text-left group"
                    >
                      {r.dir === "out"
                        ? <ArrowRight className="w-3 h-3 text-ocean-accent shrink-0" />
                        : <ArrowLeft className="w-3 h-3 text-amber-400 shrink-0" />}
                      <span className="text-gray-400 font-medium shrink-0">{r.rel}</span>
                      <span className="text-gray-600">·</span>
                      <span className="flex items-center gap-1 min-w-0 flex-1">
                        <i className="w-2 h-2 rounded-full shrink-0" style={{ background: otherAccent }} />
                        <span className="text-gray-200 group-hover:text-white truncate">{r.other}</span>
                      </span>
                      <Share2 className="w-3 h-3 text-gray-600 group-hover:text-ocean-accent shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                    </button>
                  </li>
                );
              })}
              {detail.relationships.length === 0 && (
                <li className="text-xs text-gray-600">No relationships.</li>
              )}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function Select({
  label, value, onChange, options,
}: {
  label: string; value: string; onChange: (v: string) => void; options: string[];
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] uppercase tracking-wider text-gray-500">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-ocean-card border border-ocean-border/60 rounded-xl px-3 py-2.5 text-sm text-white min-w-[170px] focus:outline-none focus:border-ocean-accent/60"
      >
        <option value="">Any {label.toLowerCase()}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-3 text-[11px] text-gray-400">
      {Object.entries(TYPE_COLOR).map(([type, color]) => (
        <span key={type} className="flex items-center gap-1.5">
          <i className="w-2.5 h-2.5 rounded-full" style={{ background: color }} /> {type}
        </span>
      ))}
      <span className="ml-auto text-gray-600">drag nodes · scroll to zoom</span>
    </div>
  );
}

function NavLink({
  href, icon, label, active = false,
}: {
  href: string; icon: React.ReactNode; label: string; active?: boolean;
}) {
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
