"use client";
/**
 * AuditTrail (L4 HITL) — the append-only history of one decision: the AI's
 * automated outcome, the review_requested pause, and each human verdict. Lazily
 * fetches GET /decisions/{id}/audit when expanded, and refetches when `refreshKey`
 * changes (e.g. after a review is submitted).
 */
import { useEffect, useState } from "react";
import { History, ChevronDown, ChevronRight, Cpu, UserCheck, UserX, Replace, ShieldQuestion } from "lucide-react";

import { decisionApi } from "@/lib/api";
import type { DecisionAudit } from "@/types";

const ACTION_STYLE: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  review_requested: { color: "#f59e0b", icon: <ShieldQuestion className="w-3.5 h-3.5" />, label: "Review requested" },
  review_approve: { color: "#22c55e", icon: <UserCheck className="w-3.5 h-3.5" />, label: "Approved" },
  review_override: { color: "#00d4ff", icon: <Replace className="w-3.5 h-3.5" />, label: "Overridden" },
  review_reject: { color: "#ef4444", icon: <UserX className="w-3.5 h-3.5" />, label: "Rejected" },
};

export default function AuditTrail({ decisionId, refreshKey }: { decisionId: string; refreshKey?: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<DecisionAudit[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    decisionApi
      .audit(decisionId)
      .then((r) => { if (alive) setRows(r); })
      .catch(() => { if (alive) setRows([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open, decisionId, refreshKey]);

  return (
    <div className="glass rounded-2xl border border-ocean-border/50 p-4">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 text-sm font-semibold text-white"
      >
        {open ? <ChevronDown className="w-4 h-4 text-ocean-accent" /> : <ChevronRight className="w-4 h-4 text-ocean-accent" />}
        <History className="w-4 h-4 text-ocean-accent" /> Audit trail
        {rows.length > 0 && <span className="text-[10px] text-gray-500">({rows.length})</span>}
      </button>

      {open && (
        <div className="mt-3 space-y-2">
          {loading && <p className="text-xs text-gray-500">Loading…</p>}
          {!loading && rows.length === 0 && (
            <p className="text-xs text-gray-500">No review activity — this decision was made automatically.</p>
          )}
          {rows.map((a) => {
            const s = ACTION_STYLE[a.action] || { color: "#64748b", icon: <Cpu className="w-3.5 h-3.5" />, label: a.action };
            return (
              <div key={a.audit_id} className="flex items-start gap-2.5 rounded-lg border border-ocean-border/40 px-2.5 py-2">
                <span className="mt-0.5 shrink-0" style={{ color: s.color }}>{s.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-white">{s.label}</span>
                    <span className="text-[10px] text-gray-500 shrink-0">
                      {a.ts ? new Date(a.ts).toLocaleString() : ""}
                    </span>
                  </div>
                  <div className="text-[11px] text-gray-400">
                    {a.actor || "system"}
                    {a.from_state && a.to_state && (
                      <span className="text-gray-600"> · {a.from_state} → {a.to_state}</span>
                    )}
                  </div>
                  {a.reason && <div className="text-[11px] text-gray-300 mt-0.5">Reason: {a.reason}</div>}
                  {a.comments && <div className="text-[11px] text-gray-400 mt-0.5 italic">“{a.comments}”</div>}
                  {a.evidence && a.evidence.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {a.evidence.map((e, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-ocean-accent/10 text-ocean-accent border border-ocean-accent/20">
                          {e.label}{e.ref ? `: ${e.ref}` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
