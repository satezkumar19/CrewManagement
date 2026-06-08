"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { Anchor, ArrowLeft, RefreshCw, ChevronDown, ChevronUp, Zap, Clock } from "lucide-react";
import { workflowApi } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useWorkflowStore } from "@/store/workflowStore";
import AgentConsole from "@/components/agents/AgentConsole";
import AgentCapabilitiesCards from "@/components/agents/AgentCapabilitiesCards";
import type { WorkflowState, AgentExecution } from "@/types";
import { cn, statusBg, statusColor, agentIcon, formatDuration, formatCost } from "@/lib/utils";

export default function WorkflowPage() {
  const [workflows, setWorkflows] = useState<WorkflowState[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const { agentStates } = useWorkflowStore();
  useWebSocket();

  useEffect(() => {
    loadWorkflows();
    const interval = setInterval(loadWorkflows, 5000);
    return () => clearInterval(interval);
  }, []);

  const loadWorkflows = async () => {
    try {
      const data = await workflowApi.listWorkflows(20);
      setWorkflows(data);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-ocean-gradient">
      <nav className="border-b border-ocean-border bg-ocean-card/90 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-screen-2xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-accent-gradient flex items-center justify-center">
              <Anchor className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold gradient-text">MarineCrewOS</h1>
              <p className="text-xs text-gray-500">Workflow Visualization</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/" className="flex items-center gap-2 px-4 py-2 text-gray-400 hover:text-white text-sm">
              <ArrowLeft className="w-4 h-4" /> Dashboard
            </Link>
            <Link href="/graph" className="px-4 py-2 text-gray-400 hover:text-white text-sm">Graph</Link>
            <Link href="/monitoring" className="px-4 py-2 text-gray-400 hover:text-white text-sm">Monitoring</Link>
            <button onClick={loadWorkflows} className="p-2 text-gray-400 hover:text-white">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </nav>

      <div className="max-w-screen-2xl mx-auto px-6 py-8 space-y-6">
        {/* Agents — tools & skills cards */}
        <AgentCapabilitiesCards />

        {/* Agent Flow Graph */}
        <AgentFlowGraph agentStates={agentStates} />

        {/* Agent Console — live context / loop / skill activity */}
        <AgentConsole />

        {/* Workflow List */}
        <div className="glass rounded-2xl overflow-hidden">
          <div className="px-6 py-4 border-b border-ocean-border flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Workflow History</h2>
            <span className="text-sm text-gray-400">{workflows.length} workflows</span>
          </div>

          {loading ? (
            <div className="p-20 flex justify-center">
              <div className="w-8 h-8 border-4 border-ocean-accent/30 border-t-ocean-accent rounded-full animate-spin" />
            </div>
          ) : workflows.length === 0 ? (
            <div className="p-16 text-center text-gray-500">
              <p>No workflows yet. Initiate a sign-off from the Dashboard to begin.</p>
            </div>
          ) : (
            <div className="divide-y divide-ocean-border/30">
              {workflows.map((wf) => (
                <WorkflowRow
                  key={wf.workflow_id}
                  workflow={wf}
                  expanded={expanded === wf.workflow_id}
                  onToggle={() => setExpanded(expanded === wf.workflow_id ? null : wf.workflow_id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type FlowState = { status: string; tool_calls: number; current_task?: string; skills_used?: number };

function AgentFlowGraph({ agentStates }: { agentStates: Record<string, FlowState> }) {
  // Master on top; all four specialists aligned in one row beneath it. Edges fan
  // out from the master so it's unambiguous which agent is being delegated to.
  const master = { name: "Master Agent", x: 50, y: 18 };
  const specialists = [
    { name: "Crew Matching Agent", x: 14, y: 70 },
    { name: "Travel Agent", x: 38, y: 70 },
    { name: "Notification Agent", x: 62, y: 70 },
    { name: "Compliance Agent", x: 86, y: 70 },
  ];
  const activeCount = specialists.filter((s) => agentStates[s.name]?.status === "running").length;
  const totalSkills = [master, ...specialists].reduce(
    (n, s) => n + (agentStates[s.name]?.skills_used || 0),
    0
  );

  return (
    <div className="glass rounded-2xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <Zap className="w-4 h-4 text-ocean-accent" />
          Live Agent Orchestration
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-[11px] px-2 py-0.5 rounded-full border border-green-500/30 bg-green-500/10 text-green-300">
            📚 {totalSkills} skill{totalSkills === 1 ? "" : "s"} used
          </span>
          <span className="text-xs text-gray-500">
            {activeCount > 0
              ? `Master is calling ${activeCount} agent${activeCount > 1 ? "s" : ""} ↓`
              : "Master delegates to the highlighted agents ↓"}
          </span>
        </div>
      </div>

      <div className="relative h-72 bg-ocean/30 rounded-xl border border-ocean-border/30 overflow-hidden">
        {/* Delegation edges: master → each specialist. Active edges animate the
            dash flow downward (master → agent), showing the call direction. */}
        <svg className="absolute inset-0 w-full h-full pointer-events-none">
          {specialists.map((s) => {
            const status = agentStates[s.name]?.status || "idle";
            const active = status === "running";
            const done = status === "completed";
            return (
              <line
                key={s.name}
                x1="50%"
                y1="28%"
                x2={`${s.x}%`}
                y2="61%"
                stroke={active ? "#00d4ff" : done ? "#22c55e66" : "#1e3a5f"}
                strokeWidth={active ? 2.5 : 1.5}
                className={active ? "data-flow-line" : ""}
              />
            );
          })}
        </svg>

        <FlowNode node={master} state={agentStates[master.name]} isMaster />
        {specialists.map((s) => (
          <FlowNode key={s.name} node={s} state={agentStates[s.name]} />
        ))}
      </div>
    </div>
  );
}

function FlowNode({
  node,
  state,
  isMaster = false,
}: {
  node: { name: string; x: number; y: number };
  state?: FlowState;
  isMaster?: boolean;
}) {
  const status = state?.status || "idle";
  const active = status === "running";
  return (
    <div
      className="absolute"
      style={{ left: `${node.x}%`, top: `${node.y}%`, transform: "translate(-50%, -50%)" }}
    >
      {/* Inbound delegation arrow — points into the agent the master is calling */}
      {!isMaster && (
        <div className="flex justify-center -mb-1">
          <ChevronDown
            className={cn(
              "w-4 h-4 transition-colors",
              active
                ? "text-ocean-accent animate-bounce"
                : status === "completed"
                ? "text-green-500/50"
                : "text-ocean-border"
            )}
          />
        </div>
      )}
      <motion.div
        animate={active ? { scale: [1, 1.04, 1] } : {}}
        transition={{ repeat: Infinity, duration: 2 }}
        className={cn(
          "min-w-[118px] px-3 py-2 rounded-xl border text-center transition-all",
          active
            ? "bg-ocean-card border-ocean-accent/60 glow-card"
            : status === "completed"
            ? "bg-green-900/25 border-green-500/40"
            : status === "failed"
            ? "bg-red-900/25 border-red-500/40"
            : status === "waiting"
            ? "bg-orange-900/25 border-orange-500/40"
            : status === "pending"
            ? "bg-yellow-900/20 border-yellow-500/30"
            : "bg-ocean-card border-ocean-border/40"
        )}
      >
        <div className="text-lg leading-none">{agentIcon(node.name)}</div>
        <div className="text-xs font-medium text-white leading-tight mt-1">
          {node.name.replace(" Agent", "")}
        </div>
        <div className={cn("text-[10px] mt-0.5", statusColor(status))}>
          {isMaster ? `orchestrator · ${status}` : status}
        </div>
        {!!state && state.tool_calls > 0 && (
          <div className="text-[10px] text-gray-500 mt-0.5">{state.tool_calls} tool calls</div>
        )}
        {!!state?.skills_used && (
          <div className="text-[10px] text-green-400/80 mt-0.5">📚 {state.skills_used} skill{state.skills_used === 1 ? "" : "s"}</div>
        )}
      </motion.div>
    </div>
  );
}

function WorkflowRow({
  workflow, expanded, onToggle
}: {
  workflow: WorkflowState;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="w-full px-6 py-4 flex items-center gap-4 hover:bg-ocean-accent/5 transition-colors text-left"
      >
        <span className={cn("w-2 h-2 rounded-full shrink-0", {
          "bg-blue-400 animate-pulse": workflow.status === "running",
          "bg-orange-400 animate-pulse": workflow.status === "waiting",
          "bg-green-400": workflow.status === "completed",
          "bg-red-400": workflow.status === "failed",
          "bg-yellow-400": workflow.status === "paused",
          "bg-gray-500": ["pending", "cancelled"].includes(workflow.status),
        })} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-white truncate">
              {workflow.trigger || `Workflow ${workflow.workflow_id.slice(0, 8)}`}
            </span>
            <span className={cn("text-xs px-2 py-0.5 rounded border", statusBg(workflow.status))}>
              {workflow.status}
            </span>
          </div>
          <div className="flex items-center gap-4 mt-1">
            <span className="text-xs text-gray-500">
              {new Date(workflow.created_at).toLocaleString()}
            </span>
            <span className="text-xs text-gray-600">
              {workflow.agent_executions?.length || 0} agents · {workflow.total_tokens?.toLocaleString() || 0} tokens
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{formatCost(workflow.total_cost || 0)}</span>
          {expanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
        </div>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-ocean-border/30 bg-ocean/20"
          >
            <div className="p-6 grid grid-cols-2 gap-6">
              {/* Agent executions */}
              <div>
                <h4 className="text-xs font-semibold text-gray-400 uppercase mb-3">Agent Executions</h4>
                <div className="space-y-2">
                  {workflow.agent_executions?.map((exec) => (
                    <AgentExecutionCard key={exec.agent_id} execution={exec} />
                  )) || <p className="text-xs text-gray-600">No agent data yet</p>}
                </div>
              </div>

              {/* Timeline */}
              <div>
                <h4 className="text-xs font-semibold text-gray-400 uppercase mb-3">Timeline</h4>
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {workflow.timeline?.map((entry, i) => (
                    <div key={i} className="flex gap-2 text-xs">
                      <span className="text-gray-600 font-mono shrink-0">
                        {new Date(entry.timestamp).toLocaleTimeString()}
                      </span>
                      <span className="text-gray-400">{entry.event}</span>
                    </div>
                  )) || <p className="text-xs text-gray-600">No timeline data</p>}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function AgentExecutionCard({ execution }: { execution: AgentExecution }) {
  return (
    <div className={cn(
      "rounded-lg p-3 border text-xs",
      execution.status === "completed" ? "bg-green-900/20 border-green-500/20" :
      execution.status === "failed" ? "bg-red-900/20 border-red-500/20" :
      "bg-ocean/40 border-ocean-border/30"
    )}>
      <div className="flex items-center justify-between">
        <span className="font-medium text-white flex items-center gap-1.5">
          {agentIcon(execution.agent_name)} {execution.agent_name}
        </span>
        <span className={cn("px-1.5 py-0.5 rounded border", statusBg(execution.status))}>
          {execution.status}
        </span>
      </div>
      <div className="flex items-center gap-3 mt-1.5 text-gray-500">
        {execution.duration_ms && <span><Clock className="w-3 h-3 inline mr-0.5" />{formatDuration(execution.duration_ms)}</span>}
        {execution.tokens_used > 0 && <span>{execution.tokens_used.toLocaleString()} tokens</span>}
        {execution.tool_calls?.length > 0 && <span>{execution.tool_calls.length} tool calls</span>}
        {execution.confidence_score && (
          <span className="text-green-400 ml-auto">{(execution.confidence_score * 100).toFixed(0)}%</span>
        )}
      </div>
      {execution.error_message && (
        <p className="text-red-400 mt-1.5 text-xs">{execution.error_message}</p>
      )}
    </div>
  );
}
