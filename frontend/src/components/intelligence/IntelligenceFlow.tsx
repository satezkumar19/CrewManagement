"use client";
import { motion } from "framer-motion";
import {
  FileInput, Network, Users, FileText, Ship, GitMerge,
  Trophy, AlertTriangle, Send, ChevronDown,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { useWorkflowStore } from "@/store/workflowStore";

type NodeStatus = "idle" | "running" | "done" | "warn";

const STYLE: Record<NodeStatus, { box: string; dot: string; text: string }> = {
  idle:    { box: "border-ocean-border/40 bg-ocean-card/40",        dot: "bg-gray-600",            text: "text-gray-500" },
  running: { box: "border-sky-400/60 bg-sky-400/10",                dot: "bg-sky-400 animate-pulse", text: "text-sky-200" },
  done:    { box: "border-emerald-400/50 bg-emerald-400/5",         dot: "bg-emerald-400",         text: "text-emerald-200" },
  warn:    { box: "border-amber-400/60 bg-amber-400/10",            dot: "bg-amber-400",           text: "text-amber-200" },
};

function FlowNode({
  icon: Icon, title, subtitle, status,
}: { icon: LucideIcon; title: string; subtitle?: string; status: NodeStatus }) {
  const s = STYLE[status];
  return (
    <motion.div layout initial={{ opacity: 0.6 }} animate={{ opacity: 1 }}
      className={`w-full rounded-lg border px-3 py-2 flex items-center gap-2.5 ${s.box}`}>
      <Icon className={`w-4 h-4 shrink-0 ${s.text}`} />
      <div className="min-w-0 flex-1">
        <p className={`text-xs font-medium leading-tight ${status === "idle" ? "text-gray-400" : "text-white"}`}>{title}</p>
        {subtitle && <p className="text-[10px] text-gray-500 leading-tight truncate">{subtitle}</p>}
      </div>
      <span className={`w-2 h-2 rounded-full shrink-0 ${s.dot}`} />
    </motion.div>
  );
}

function Connector({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center py-0.5">
      <div className="w-px h-2 bg-ocean-border/60" />
      <ChevronDown className="w-3 h-3 text-ocean-border" />
      {label && <span className="text-[9px] uppercase tracking-wide text-gray-600 -mt-0.5">{label}</span>}
    </div>
  );
}

export default function IntelligenceFlow() {
  const { intel } = useWorkflowStore();
  const started = intel.startedAt !== null || intel.trace.length > 0;
  const result = intel.result;

  if (!started) return null;

  const inputStatus: NodeStatus = "done";
  const supervisorStatus: NodeStatus = intel.running ? "running" : result ? "done" : "idle";
  const allInvestigatorsDone = intel.investigators.every((i) => i.status === "done");
  const fusionStatus: NodeStatus = result
    ? (result.status === "no_crew_found" ? "warn" : "done")
    : allInvestigatorsDone ? "running" : "idle";
  const outputStatus: NodeStatus =
    result?.status === "matched" ? "done" : result?.status === "no_crew_found" ? "warn" : "idle";
  const notifyStatus: NodeStatus = result && result.notifications.length ? "done" : "idle";

  const invIcon = { crew: Users, contract: FileText, vessel: Ship } as const;
  const invShort = { crew: "Crew", contract: "Contract", vessel: "Vessel" } as const;

  return (
    <div className="rounded-lg bg-ocean-card/40 border border-ocean-border/40 p-3">
      <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Workflow</p>

      {/* INPUT */}
      <FlowNode icon={FileInput} title="Sign-off vacancy" status={inputStatus}
        subtitle={intel.vacatedRank ? `${intel.vacatedRank}${intel.port ? ` @ ${intel.port}` : ""}` : undefined} />
      <Connector />

      {/* SUPERVISOR */}
      <FlowNode icon={Network} title="Supervisor"
        subtitle="delegates to 3 investigators" status={supervisorStatus} />
      <Connector label="parallel" />

      {/* 3 INVESTIGATORS (fan-out) */}
      <div className="grid grid-cols-3 gap-1.5">
        {intel.investigators.map((inv) => {
          const st: NodeStatus = inv.status === "idle" ? "idle" : inv.status === "running" ? "running" : "done";
          const s = STYLE[st];
          const Icon = invIcon[inv.key];
          return (
            <motion.div key={inv.key} layout
              className={`rounded-lg border px-1.5 py-1.5 text-center ${s.box}`}>
              <Icon className={`w-3.5 h-3.5 mx-auto ${s.text}`} />
              <p className={`text-[10px] mt-0.5 ${st === "idle" ? "text-gray-500" : "text-white"}`}>{invShort[inv.key]}</p>
              <p className="text-[9px] text-gray-500 leading-none">
                {inv.status === "done" && inv.assessed != null
                  ? `${inv.eligible}/${inv.assessed} ok`
                  : inv.status === "running" ? "…" : "idle"}
              </p>
            </motion.div>
          );
        })}
      </div>
      <Connector label="fuse" />

      {/* FUSION */}
      <FlowNode icon={GitMerge} title="Fusion & ranking"
        subtitle="hard gates · weighted blend · top-3" status={fusionStatus} />
      <Connector />

      {/* OUTPUT */}
      {outputStatus === "warn" ? (
        <FlowNode icon={AlertTriangle} title="No eligible crew"
          subtitle={`${result?.pool_size ?? 0} assessed · 0 shortlisted`} status="warn" />
      ) : (
        <FlowNode icon={Trophy} title={result ? `Matched · top ${result.candidates.length}` : "Ranked output"}
          subtitle={result ? `${result.pool_size} assessed · ${result.disqualified} filtered` : undefined}
          status={outputStatus} />
      )}
      <Connector />

      {/* NOTIFY */}
      <FlowNode icon={Send} title="Operators notified"
        subtitle={result ? result.notifications.map((n) => `${n.role.split(" ")[0]}·${n.channel}`).join("  ") : undefined}
        status={notifyStatus} />
    </div>
  );
}
