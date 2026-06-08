"use client";
/**
 * SimilarCrewPanel (L4 #3 — Structural Embeddings) — makes the feature visible.
 *
 * Pick a crew member and see the crew most STRUCTURALLY similar to them, ranked by
 * the embedding cosine similarity that `GET /embeddings/similar/{id}` returns. The
 * backend badge shows whether the similarity ran in pgvector or the Python fallback.
 * This is the queryable face of the structural embeddings that also enrich ranking.
 */
import { useEffect, useMemo, useState } from "react";
import { Boxes, Database, Cpu } from "lucide-react";

import { crewApi, embeddingApi } from "@/lib/api";
import type { CrewMember, SimilarCrew } from "@/types";

export default function SimilarCrewPanel() {
  const [crew, setCrew] = useState<CrewMember[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [matches, setMatches] = useState<SimilarCrew[]>([]);
  const [backend, setBackend] = useState<string>("");
  const [loading, setLoading] = useState(false);

  // Load the sign-on pool once to populate the selector (these carry the richest
  // profiles, and similarity searches the same pool for available neighbors).
  useEffect(() => {
    crewApi
      .getSignOnCrew()
      .then((list) => {
        setCrew(list);
        if (list.length) setSelectedId(list[0].crew_id);
      })
      .catch(() => setCrew([]));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setLoading(true);
    embeddingApi
      .similar(selectedId, "signon", 6)
      .then((res) => {
        if (cancelled) return;
        setMatches(res.matches || []);
        setBackend(res.backend || "");
      })
      .catch(() => { if (!cancelled) { setMatches([]); setBackend(""); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const selected = useMemo(
    () => crew.find((c) => c.crew_id === selectedId),
    [crew, selectedId]
  );

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4 mb-6">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Boxes className="w-4 h-4 text-ocean-accent" /> Structural Similarity Explorer
        </h3>
        {backend && <BackendBadge backend={backend} />}
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className="text-[11px] text-gray-500 shrink-0">Most similar to</span>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          className="flex-1 bg-ocean-card border border-ocean-border/50 rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-ocean-accent/60"
        >
          {crew.length === 0 && <option value="">No crew loaded</option>}
          {crew.map((c) => (
            <option key={c.crew_id} value={c.crew_id}>
              {c.name} — {c.rank}{c.nationality ? ` · ${c.nationality}` : ""}
            </option>
          ))}
        </select>
      </div>

      {selected && (
        <p className="text-[10px] text-gray-500 mb-2">
          Nearest available crew by structural embedding (rank, grade, port, nationality, certifications, experience).
        </p>
      )}

      {loading ? (
        <p className="text-xs text-gray-500 py-2">Computing similarity…</p>
      ) : matches.length === 0 ? (
        <p className="text-xs text-gray-500 py-2">
          No similar crew found{crew.length === 0 ? " — seed crew first (scripts.seed_crew)." : "."}
        </p>
      ) : (
        <div className="space-y-1.5">
          {matches.map((m) => (
            <SimilarRow key={m.crew_id} m={m} />
          ))}
        </div>
      )}
    </div>
  );
}

function SimilarRow({ m }: { m: SimilarCrew }) {
  const pct = Math.round((m.similarity ?? 0) * 100);
  // Green for a close structural match, fading to slate as it drops.
  const color = pct >= 80 ? "#22c55e" : pct >= 55 ? "#00d4ff" : "#64748b";
  return (
    <div className="flex items-center gap-3 rounded-lg border border-ocean-border/40 px-2.5 py-1.5">
      <div className="min-w-0 w-48 shrink-0">
        <div className="text-xs text-white truncate">{m.name || m.crew_id}</div>
        <div className="text-[10px] text-gray-500 truncate">
          {[m.rank, m.nationality, m.port].filter(Boolean).join(" · ")}
        </div>
      </div>
      <div className="flex-1 h-2 rounded-full bg-ocean-border/30 overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[11px] font-semibold w-12 text-right" style={{ color }}>{pct}%</span>
    </div>
  );
}

function BackendBadge({ backend }: { backend: string }) {
  const isVector = backend === "pgvector";
  const color = isVector ? "#22c55e" : "#94a3b8";
  return (
    <span
      className="flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-md"
      style={{ color, border: `1px solid ${color}44`, background: `${color}12` }}
      title={isVector ? "Similarity computed in PostgreSQL via pgvector" : "Similarity computed in Python (fallback)"}
    >
      {isVector ? <Database className="w-3 h-3" /> : <Cpu className="w-3 h-3" />}
      {isVector ? "pgvector" : "python fallback"}
    </span>
  );
}
