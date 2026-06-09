"use client";
/**
 * ReviewPanel (L4 HITL) — the human reviewer's control for a decision that the
 * automated sign-on couldn't resolve on its own (review_status === "pending_review").
 *
 * The reviewer can APPROVE the AI's proposed candidate, OVERRIDE to a different
 * sign-on candidate, or REJECT the placement — each with a reason code, free-text
 * comments, and optional evidence. Submitting calls POST /decisions/{id}/review;
 * the resolved decision is handed back via onReviewed so the page patches it live.
 */
import { useState } from "react";
import useSWR from "swr";
import { motion } from "framer-motion";
import toast from "react-hot-toast";
import axios from "axios";
import {
  UserCheck, UserX, Replace, ShieldQuestion, Loader2, Plus, X, Cpu,
} from "lucide-react";

import { crewApi, decisionApi } from "@/lib/api";
import type { DecisionTrace, ReviewAction, ReviewEvidence } from "@/types";

const REASON_CODES = [
  "Compliance exception approved",
  "Documents verified manually",
  "Operational necessity",
  "Visa / medical risk",
  "Better-fit candidate",
  "Insufficient evidence",
  "Other",
];

const ACTIONS: { key: ReviewAction; label: string; Icon: typeof UserCheck; color: string }[] = [
  { key: "approve", label: "Approve", Icon: UserCheck, color: "#22c55e" },
  { key: "override", label: "Override", Icon: Replace, color: "#00d4ff" },
  { key: "reject", label: "Reject", Icon: UserX, color: "#ef4444" },
];

export default function ReviewPanel({
  decision,
  onReviewed,
}: {
  decision: DecisionTrace;
  onReviewed?: (d: DecisionTrace) => void;
}) {
  const [action, setAction] = useState<ReviewAction>("approve");
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState(REASON_CODES[0]);
  const [comments, setComments] = useState("");
  const [evidence, setEvidence] = useState<ReviewEvidence[]>([]);
  const [evLabel, setEvLabel] = useState("");
  const [evRef, setEvRef] = useState("");
  const [overrideId, setOverrideId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Override candidate options = the sign-on pool (who can actually be signed on).
  const { data: signOn } = useSWR(
    action === "override" ? "review-signon" : null,
    crewApi.getSignOnCrew
  );

  const proposal = decision.ai_proposal ?? ({} as NonNullable<DecisionTrace["ai_proposal"]>);
  const trigger = decision.review_trigger;

  const addEvidence = () => {
    if (!evLabel.trim()) return;
    setEvidence((e) => [...e, { label: evLabel.trim(), ref: evRef.trim() || undefined }]);
    setEvLabel("");
    setEvRef("");
  };

  const submit = async () => {
    if (action === "override" && !overrideId) {
      toast.error("Pick a candidate to override to");
      return;
    }
    setSubmitting(true);
    try {
      const updated = await decisionApi.review(decision.decision_id, {
        action,
        reviewer: reviewer.trim() || undefined,
        reason,
        comments: comments.trim() || undefined,
        evidence: evidence.length ? evidence : undefined,
        override_crew_id: action === "override" ? overrideId : undefined,
      });
      toast.success(
        action === "reject" ? "Decision rejected" : action === "override" ? "Overridden & signed on" : "Approved & signed on"
      );
      onReviewed?.(updated);
    } catch (err: unknown) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data as { detail?: string })?.detail || err.message
        : "Failed to submit review";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass rounded-2xl border border-amber-500/40 bg-amber-900/10 p-4"
    >
      <div className="flex items-center gap-2 mb-1">
        <ShieldQuestion className="w-4 h-4 text-amber-300" />
        <h3 className="text-sm font-semibold text-white">Human review required</h3>
        {trigger && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
            {trigger === "warning" ? "conditional pass" : trigger === "exhausted" ? "all candidates failed" : trigger}
          </span>
        )}
      </div>
      {decision.pending_reason && (
        <p className="text-[11px] text-gray-300 mb-3">{decision.pending_reason}</p>
      )}

      {/* AI's proposal */}
      {(proposal.name || proposal.crew_id) && (
        <div className="flex items-center gap-2 mb-3 text-xs text-gray-300">
          <Cpu className="w-3.5 h-3.5 text-ocean-accent shrink-0" />
          <span className="text-gray-500">AI proposed</span>
          <span className="text-white font-medium">{proposal.name || proposal.crew_id}</span>
          {proposal.rank && <span className="text-gray-500">· {proposal.rank}</span>}
          {proposal.compliance_status && (
            <span className="text-amber-300">
              · {proposal.compliance_status}
              {proposal.compliance_score != null ? ` (${proposal.compliance_score}%)` : ""}
            </span>
          )}
        </div>
      )}

      {/* Action selector */}
      <div className="grid grid-cols-3 gap-1.5 mb-3">
        {ACTIONS.map(({ key, label, Icon, color }) => {
          const on = action === key;
          return (
            <button
              key={key}
              onClick={() => setAction(key)}
              className="flex items-center justify-center gap-1.5 py-2 rounded-xl text-xs font-medium border transition"
              style={{
                color: on ? "#fff" : color,
                background: on ? `${color}22` : "transparent",
                borderColor: on ? color : "rgba(148,163,184,0.25)",
              }}
            >
              <Icon className="w-3.5 h-3.5" /> {label}
            </button>
          );
        })}
      </div>

      {/* Override candidate picker */}
      {action === "override" && (
        <div className="mb-3">
          <label className="text-[10px] uppercase tracking-wider text-gray-500">Sign on instead</label>
          <select
            value={overrideId}
            onChange={(e) => setOverrideId(e.target.value)}
            className="mt-1 w-full bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-2 text-sm text-white focus:border-ocean-accent outline-none"
          >
            <option value="">Select a sign-on candidate…</option>
            {(signOn ?? []).map((c) => (
              <option key={c.crew_id} value={c.crew_id}>
                {c.name} · {c.rank} · {c.crew_id}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Reviewer + reason */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div>
          <label className="text-[10px] uppercase tracking-wider text-gray-500">Reviewer</label>
          <input
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="Your name"
            className="mt-1 w-full bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-2 text-sm text-white focus:border-ocean-accent outline-none"
          />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-wider text-gray-500">Reason</label>
          <select
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="mt-1 w-full bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-2 text-sm text-white focus:border-ocean-accent outline-none"
          >
            {REASON_CODES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Comments */}
      <div className="mb-3">
        <label className="text-[10px] uppercase tracking-wider text-gray-500">Comments</label>
        <textarea
          value={comments}
          onChange={(e) => setComments(e.target.value)}
          rows={2}
          placeholder="Rationale for the decision…"
          className="mt-1 w-full bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-2 text-sm text-white focus:border-ocean-accent outline-none resize-none"
        />
      </div>

      {/* Evidence */}
      <div className="mb-3">
        <label className="text-[10px] uppercase tracking-wider text-gray-500">Evidence (optional)</label>
        {evidence.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1 mb-1.5">
            {evidence.map((e, i) => (
              <span key={i} className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-ocean-accent/10 text-ocean-accent border border-ocean-accent/20">
                {e.label}{e.ref ? `: ${e.ref}` : ""}
                <button onClick={() => setEvidence((arr) => arr.filter((_, j) => j !== i))}>
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1.5">
          <input
            value={evLabel}
            onChange={(e) => setEvLabel(e.target.value)}
            placeholder="Label (e.g. Visa renewal)"
            className="flex-1 bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-1.5 text-xs text-white focus:border-ocean-accent outline-none"
          />
          <input
            value={evRef}
            onChange={(e) => setEvRef(e.target.value)}
            placeholder="Ref / link"
            className="flex-1 bg-ocean-card border border-ocean-border/60 rounded-lg px-2.5 py-1.5 text-xs text-white focus:border-ocean-accent outline-none"
          />
          <button
            onClick={addEvidence}
            className="p-1.5 rounded-lg border border-ocean-border/60 text-gray-300 hover:text-white"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <button
        onClick={submit}
        disabled={submitting}
        className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold bg-accent-gradient text-white shadow-lg disabled:opacity-60 transition"
      >
        {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Submit review
      </button>
    </motion.div>
  );
}
