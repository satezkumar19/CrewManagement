"use client";
import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Terminal, Trash2, ChevronRight, ChevronDown } from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";
import { monitoringApi, type AgentSkills } from "@/lib/api";
import { cn, agentIcon } from "@/lib/utils";
import type { WSEvent } from "@/types";

const AGENTS = [
  "Master Agent",
  "Crew Matching Agent",
  "Travel Agent",
  "Notification Agent",
  "Compliance Agent",
];

type Lane = "context" | "loop" | "skill";
type Kind = "all" | "tools" | "skills" | "context";

function lanesFor(e: WSEvent): Lane[] {
  const data = e.data || {};
  const cat = data.category as Lane | undefined;
  const lanes = new Set<Lane>();
  if (cat === "context" || cat === "loop" || cat === "skill") lanes.add(cat);
  if (data.model_usage) lanes.add("context");
  if (lanes.size === 0) {
    const t = (e.event_type || "").toLowerCase();
    if (t.includes("compact")) lanes.add("context");
    else if (t.includes("skill")) lanes.add("skill");
    else lanes.add("loop");
  }
  return Array.from(lanes);
}

function humanizeType(t: string): string {
  return (t || "event").replace(/^(agent|span|session)[._]/, "").replace(/[._]/g, " ");
}

function shortName(agent?: string): string {
  return (agent || "Master Agent").replace(" Agent", "");
}

function money(n: unknown): string {
  return typeof n === "number" ? `$${n.toLocaleString()}` : "—";
}

type Narration = { icon: string; text: string; milestone?: boolean };

function narrate(e: WSEvent, iter?: number): Narration | null {
  const d = (e.data || {}) as Record<string, unknown>;
  const t = e.event_type || "";

  // Skill access (a read/bash that opened a SKILL.md) — tagged category:"skill"
  // by the backend regardless of event type. Log it as a labeled skill line.
  if (d.category === "skill") {
    const name = (d.skill as string) || "";
    return {
      icon: "📚",
      text: name ? `used skill: ${name}` : "loaded a skill package",
      milestone: true,
    };
  }

  switch (t) {
    case "workflow_created":
      return { icon: "🟢", text: "Sign-off initiated — Master Agent invoked", milestone: true };
    case "master_routing":
      return { icon: "🧭", text: `Master delegating → ${(d.action as string) || "specialists"}` };
    case "master_waiting":
      return { icon: "⏸️", text: "Phase 1 complete — awaiting sign-on confirmation", milestone: true };
    case "sign_on_initiated":
      return { icon: "🟢", text: "Sign-on confirmed — compliance check starting", milestone: true };
    case "auto_compliance":
      return { icon: "🔗", text: (d.message as string) || "auto-proceeding to compliance for the matched crew", milestone: true };
    case "crew_signed_on":
      return {
        icon: "🎉",
        text: (d.message as string) || `${d.crew_name} cleared compliance — signed on`,
        milestone: true,
      };
    case "sign_on_rejected":
      return {
        icon: "⛔",
        text: (d.message as string) || `${d.crew_name} did not clear compliance — not signed on`,
        milestone: true,
      };
    case "crew_updated":
      return null; // internal pool bookkeeping — not user-facing
    case "agent_started":
      return { icon: "▶️", text: `${shortName(e.agent_name)} started` };
    case "agent_completed":
      return { icon: "✔️", text: `${shortName(e.agent_name)} completed` };
    case "workflow_completed":
      return {
        icon: "🏁",
        text: `Workflow complete${d.compliance_status ? ` — compliance ${d.compliance_status}` : ""}`,
        milestone: true,
      };
    case "workflow_failed":
      return { icon: "❌", text: `Workflow failed: ${(d.error as string) || "unknown"}`, milestone: true };
    case "agent_thinking": {
      const tx = ((d.text as string) || "").trim();
      return tx ? { icon: "💭", text: tx.slice(0, 150) } : null;
    }
    case "agent_message": {
      const tx = ((d.text as string) || "").trim();
      return tx ? { icon: "💬", text: tx.slice(0, 180) } : null;
    }
    case "model_usage": {
      const m = (d.model_usage || {}) as Record<string, number>;
      return {
        icon: "🧠",
        text: `model turn — in ${m.input_tokens || 0} / out ${m.output_tokens || 0} tok${
          m.cache_read_input_tokens ? `, cache_read ${m.cache_read_input_tokens}` : ""
        }`,
      };
    }
    case "agent_tool_use":
      return null; // superseded by the richer tool_called line
    case "tool_called":
      return narrateTool(d, iter);
  }

  if (t.includes("compact")) {
    const pre = d.pre_compaction_tokens as number | undefined;
    return { icon: "♻️", text: pre ? `context compacted (~${pre.toLocaleString()} tok summarized)` : "context compacted" };
  }
  if (t === "agent.custom_tool_use") return null;
  return { icon: "•", text: humanizeType(t) };
}

function narrateTool(d: Record<string, unknown>, iter?: number): Narration | null {
  const tool = d.tool as string;
  const inp = (d.input || {}) as Record<string, unknown>;
  const out = (d.output || {}) as Record<string, unknown>;

  switch (tool) {
    case "searchCrew": {
      const n = (out.found as number) ?? ((out.candidates as unknown[]) || []).length;
      return {
        icon: "🔍",
        text: `searched sign-on pool for ${(inp.rank as string) || "crew"}${inp.port ? ` near ${inp.port}` : ""} → ${n} candidates`,
      };
    }
    case "rankCrew": {
      const top = (((out.ranked_candidates as Record<string, unknown>[]) || [])[0]) as Record<string, unknown> | undefined;
      if (top) {
        return {
          icon: "✅",
          text: `best match: ${top.name} — ${top.rank}, ${top.confidence_score}% confidence${iter ? ` (found on iteration ${iter})` : ""}`,
          milestone: true,
        };
      }
      return { icon: "📊", text: "ranked candidates" };
    }
    case "getCrewProfile":
      return { icon: "📋", text: `fetched full profile for ${(inp.crew_id as string) || "candidate"}` };
    case "generateTicket":
      return {
        icon: "🎫",
        text: `booked ${out.airline || "flight"} ${out.flight_number || ""}: ${out.departure_port || "?"}→${out.destination || "home"} on ${out.departure_date || "?"} (${money(out.ticket_price_usd)})`,
      };
    case "generatePortClearance":
      return {
        icon: "📄",
        text: `port clearance ${out.clearance_id || ""} — ${out.authority || out.port || ""} · ${out.status || ""}`,
      };
    case "createTravelSummary":
      return {
        icon: "📦",
        text: `travel package ${out.summary_id || ""} assembled (${((out.documents as unknown[]) || []).length} docs)`,
        milestone: true,
      };
    case "sendMail":
      return {
        icon: "✉️",
        text: `mail to ${out.to || inp.to}: "${(inp.subject as string) || ""}" — ${out.status || "sent"}`,
        milestone: out.status === "Delivered",
      };
    case "createNotificationLog":
      return { icon: "🗒️", text: `logged ${(out.total_notifications as number) ?? "all"} notifications` };
    case "validateDocuments": {
      const n = (out.total_checks as number) ?? ((out.checks as unknown[]) || []).length;
      return { icon: "🛡️", text: `validated ${n} documents for ${inp.crew_name || inp.crew_id || "crew"}` };
    }
    case "checkPortRestrictions": {
      const issues = (out.issues as unknown[]) || [];
      return {
        icon: "⚓",
        text: `port ${out.port || inp.port}: ${out.port_cleared ? "cleared" : "issues found"}${issues.length ? ` (${issues.length})` : ""}`,
      };
    }
    case "generateComplianceReport": {
      const s = out.overall_status as string;
      const ic = s === "passed" ? "✅" : s === "warning" ? "⚠️" : "❌";
      return {
        icon: ic,
        text: `compliance ${s || "?"} — ${(out.compliance_score as number) ?? "?"}% · ${out.recommendation || ""}`,
        milestone: true,
      };
    }
    default:
      return { icon: "🔧", text: tool };
  }
}

type TraceRow = { e: WSEvent; lanes: Lane[]; iter?: number; isTool: boolean } & Narration;

export default function AgentConsole() {
  const { events, clearEvents } = useWorkflowStore();
  const [kind, setKind] = useState<Kind>("all");
  const [agent, setAgent] = useState<string>("all");
  const [pick, setPick] = useState<string>("all"); // "all" | "tool:<name>" | "skill:<name>"
  const [showCaps, setShowCaps] = useState(false);
  const [skills, setSkills] = useState<AgentSkills[]>([]);

  // Selecting a specific agent reveals its tools/skills as a drill-down; reset
  // that drill-down whenever the agent changes.
  const selectAgent = (a: string) => {
    setAgent(a);
    setPick("all");
  };

  const resetFilters = () => {
    setKind("all");
    setAgent("all");
    setPick("all");
  };

  useEffect(() => {
    monitoringApi.getAgentSkills().then(setSkills).catch(() => setSkills([]));
  }, []);

  // Oldest→newest: assign each agent's tool calls an iteration number (the buffer
  // is reset per workflow, so iterations restart at 1 each run), narrate, then
  // present newest-first.
  const trace = useMemo<TraceRow[]>(() => {
    const chron = [...events].reverse();
    const iterByAgent: Record<string, number> = {};
    const seenLifecycle = new Set<string>(); // each agent starts/completes once
    const built: TraceRow[] = [];
    let lastKey = "";
    for (const e of chron) {
      const isTool = e.event_type === "tool_called";
      let iter: number | undefined;
      if (isTool) {
        const a = e.agent_name || "Agent";
        iterByAgent[a] = (iterByAgent[a] || 0) + 1;
        iter = iterByAgent[a];
      }
      const n = narrate(e, iter);
      if (!n) continue;
      const agentN = e.agent_name || "";
      const key = `${agentN}::${n.text}`;
      // Collapse back-to-back identical lines (e.g. duplicate status events).
      if (key === lastKey) continue;
      // thread_created + thread_status_running both narrate as "<agent> started";
      // keep only the first start/complete per agent.
      if (e.event_type === "agent_started" || e.event_type === "agent_completed") {
        const lk = `${e.event_type}::${agentN}`;
        if (seenLifecycle.has(lk)) continue;
        seenLifecycle.add(lk);
      }
      built.push({ e, lanes: lanesFor(e), iter, isTool, ...n });
      lastKey = key;
    }
    return built.reverse();
  }, [events]);

  const kindCounts = useMemo(() => {
    let tools = 0, skillsN = 0, context = 0;
    trace.forEach((r) => {
      if (r.isTool) tools += 1;
      if (r.lanes.includes("skill")) skillsN += 1;
      if (r.lanes.includes("context")) context += 1;
    });
    return { all: trace.length, tools, skills: skillsN, context };
  }, [trace]);

  const matchesKind = (r: TraceRow): boolean => {
    if (kind === "all") return true;
    if (kind === "tools") return r.isTool;
    if (kind === "skills") return r.lanes.includes("skill");
    if (kind === "context") return r.lanes.includes("context");
    return true;
  };
  const matchesPick = (r: TraceRow): boolean => {
    if (pick === "all") return true;
    if (pick.startsWith("tool:")) {
      const name = pick.slice(5);
      return r.isTool && r.e.data?.tool === name;
    }
    if (pick.startsWith("skill:")) {
      const name = pick.slice(6).toLowerCase();
      if (!r.lanes.includes("skill")) return false;
      // Prefer the resolved skill name the backend attaches; fall back to the raw
      // access path/input mentioning it.
      const resolved = ((r.e.data?.skill as string) || "").toLowerCase();
      if (resolved) return resolved === name;
      return JSON.stringify((r.e.data?.input ?? r.e.data) || {}).toLowerCase().includes(name);
    }
    return true;
  };
  const visible = trace.filter(
    (r) => matchesKind(r) && (agent === "all" || r.e.agent_name === agent) && matchesPick(r)
  );

  // Skills each agent ACTUALLY loaded this run (resolved name on the relayed skill
  // events). The configured/available list (from the API) often uses a different
  // label than what shows up in the access path, so we surface both as filters.
  const usedSkillsByAgent = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const e of events) {
      if ((e.data as Record<string, unknown>)?.category !== "skill") continue;
      const a = e.agent_name || "Master Agent";
      const s = (e.data?.skill as string) || "";
      if (!s) continue;
      (m[a] ||= []);
      if (!m[a].includes(s)) m[a].push(s);
    }
    return m;
  }, [events]);

  const selectedCaps = agent === "all" ? undefined : skills.find((a) => a.name === agent);
  const usedSkills = agent === "all" ? [] : usedSkillsByAgent[agent] || [];
  // Union: configured skills + ones actually used (the used ones are what populate).
  const skillChips = Array.from(new Set([...(selectedCaps?.skills || []), ...usedSkills]));

  const KIND_TABS: Array<{ key: Kind; label: string; count: number }> = [
    { key: "all", label: "All", count: kindCounts.all },
    { key: "tools", label: "Tools", count: kindCounts.tools },
    { key: "skills", label: "Skills", count: kindCounts.skills },
    { key: "context", label: "Context", count: kindCounts.context },
  ];

  return (
    <div className="glass rounded-2xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-ocean-border flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <Terminal className="w-4 h-4 text-ocean-accent" />
            Agent Console
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Iteration-by-iteration trace · auto-clears when a new sign-off starts
          </p>
        </div>
        <button
          onClick={clearEvents}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white border border-ocean-border/50 rounded-lg hover:border-ocean-border transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" /> Clear
        </button>
      </div>

      {/* Filters: kind (what) + agent (who) */}
      <div className="px-5 py-3 border-b border-ocean-border/50 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] uppercase tracking-wider text-gray-600 w-12">show</span>
          {KIND_TABS.map((k) => (
            <button
              key={k.key}
              onClick={() => setKind(k.key)}
              className={cn(
                "px-3 py-1 rounded-lg text-xs border transition-colors",
                kind === k.key
                  ? "bg-ocean-accent/15 border-ocean-accent/40 text-white"
                  : "border-ocean-border/40 text-gray-400 hover:text-white hover:border-ocean-border"
              )}
            >
              {k.label} <span className="text-gray-500">{k.count}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] uppercase tracking-wider text-gray-600 w-12">agent</span>
          <button
            onClick={() => selectAgent("all")}
            className={cn(
              "px-3 py-1 rounded-lg text-xs border transition-colors",
              agent === "all"
                ? "bg-ocean-accent/15 border-ocean-accent/40 text-white"
                : "border-ocean-border/40 text-gray-400 hover:text-white hover:border-ocean-border"
            )}
          >
            All
          </button>
          {AGENTS.map((a) => (
            <button
              key={a}
              onClick={() => selectAgent(a)}
              className={cn(
                "flex items-center gap-1 px-3 py-1 rounded-lg text-xs border transition-colors",
                agent === a
                  ? "bg-ocean-accent/15 border-ocean-accent/40 text-white"
                  : "border-ocean-border/40 text-gray-400 hover:text-white hover:border-ocean-border"
              )}
            >
              <span>{agentIcon(a)}</span>
              {shortName(a)}
            </button>
          ))}
        </div>

        {/* Drill-down: a specific agent's own tools (blue) and skills (green) */}
        {selectedCaps && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-gray-600 w-12">in {shortName(agent)}</span>
            <button
              onClick={() => setPick("all")}
              className={cn(
                "px-3 py-1 rounded-lg text-xs border transition-colors",
                pick === "all"
                  ? "bg-ocean-accent/15 border-ocean-accent/40 text-white"
                  : "border-ocean-border/40 text-gray-400 hover:text-white hover:border-ocean-border"
              )}
            >
              All
            </button>
            {selectedCaps.tools.map((tname) => (
              <button
                key={`t-${tname}`}
                onClick={() => {
                  setPick(`tool:${tname}`);
                  setKind("all");
                }}
                className={cn(
                  "px-2.5 py-1 rounded-lg text-xs border font-mono transition-colors",
                  pick === `tool:${tname}`
                    ? "bg-blue-500/25 border-blue-400/50 text-blue-100"
                    : "bg-blue-500/10 border-blue-500/25 text-blue-300 hover:border-blue-400/50"
                )}
              >
                {tname}
              </button>
            ))}
            {skillChips.map((s) => {
              const used = usedSkills.includes(s);
              return (
                <button
                  key={`s-${s}`}
                  onClick={() => {
                    setPick(`skill:${s}`);
                    setKind("all");
                  }}
                  title={used ? "loaded this run" : "available, not used this run"}
                  className={cn(
                    "px-2.5 py-1 rounded-lg text-xs border font-mono transition-colors",
                    pick === `skill:${s}`
                      ? "bg-green-500/25 border-green-400/50 text-green-100"
                      : used
                      ? "bg-green-500/15 border-green-500/40 text-green-200 hover:border-green-400/60"
                      : "bg-green-500/5 border-green-500/15 text-green-300/60 hover:border-green-400/40"
                  )}
                >
                  {used && "📚 "}{s}
                </button>
              );
            })}
            {skillChips.length === 0 && selectedCaps.tools.length === 0 && (
              <span className="text-xs text-gray-600 italic">no tools or skills</span>
            )}
          </div>
        )}
      </div>

      {/* Collapsible capabilities (tools vs skills per agent) */}
      <div className="border-b border-ocean-border/50">
        <button
          onClick={() => setShowCaps((v) => !v)}
          className="w-full px-5 py-2 flex items-center gap-1.5 text-xs text-gray-400 hover:text-white"
        >
          {showCaps ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          Agent capabilities — <span className="text-blue-300">tools</span> vs{" "}
          <span className="text-green-300">skills</span>
        </button>
        <AnimatePresence>
          {showCaps && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden bg-ocean/20"
            >
              <div className="px-5 pb-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {skills.map((a) => (
                  <div key={a.key} className="rounded-lg bg-ocean/50 border border-ocean-border/40 p-2.5 text-xs">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <span>{agentIcon(a.name)}</span>
                      <span className="text-gray-200 font-medium">{shortName(a.name)}</span>
                    </div>
                    <div className="flex items-start gap-1.5 mb-1">
                      <span className="text-gray-500 w-9 shrink-0">tools</span>
                      <span className="flex flex-wrap gap-1">
                        {a.tools.length ? (
                          a.tools.map((tname) => (
                            <span key={tname} className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/25 font-mono">
                              {tname}
                            </span>
                          ))
                        ) : (
                          <span className="text-gray-600 italic">none</span>
                        )}
                      </span>
                    </div>
                    <div className="flex items-start gap-1.5">
                      <span className="text-gray-500 w-9 shrink-0">skills</span>
                      <span className="flex flex-wrap gap-1">
                        {a.skills.length ? (
                          a.skills.map((s) => (
                            <span key={s} className="px-1.5 py-0.5 rounded bg-green-500/15 text-green-300 border border-green-500/25 font-mono">
                              {s}
                            </span>
                          ))
                        ) : (
                          <span className="text-gray-600 italic">none</span>
                        )}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Trace log */}
      <div className="max-h-[30rem] overflow-y-auto text-xs">
        {visible.length === 0 ? (
          trace.length === 0 ? (
            <div className="p-10 text-center text-gray-600 font-mono">
              No activity yet — initiate a sign-off from the Dashboard to watch the agents
              work iteration by iteration.
            </div>
          ) : (
            <div className="p-10 text-center text-gray-500 font-mono space-y-3">
              <p>
                {trace.length} event{trace.length === 1 ? "" : "s"} hidden by the current filter
                {kind !== "all" && ` · ${kind}`}
                {agent !== "all" && ` · ${shortName(agent)}`}
                {pick !== "all" && ` · ${pick.replace(":", " ")}`}
              </p>
              <button
                onClick={resetFilters}
                className="px-3 py-1.5 rounded-lg text-xs border border-ocean-accent/40 bg-ocean-accent/15 text-white hover:border-ocean-accent transition-colors"
              >
                Reset filters
              </button>
            </div>
          )
        ) : (
          <AnimatePresence initial={false}>
            {visible.map((r, i) => (
              <motion.div
                key={`${r.e.timestamp ?? "t"}-${r.e.event_type ?? "e"}-${i}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                className={cn(
                  "flex items-start gap-2 px-5 py-1.5 border-b border-ocean-border/20 hover:bg-ocean-accent/5 font-mono",
                  r.milestone && "bg-ocean-accent/[0.07] border-l-2 border-l-ocean-accent"
                )}
              >
                <span className="text-gray-600 shrink-0 tabular-nums">
                  {r.e.timestamp ? new Date(r.e.timestamp).toLocaleTimeString() : "--:--:--"}
                </span>
                {/* iteration badge for tool calls; lane tag for skill/context; else spacer */}
                {r.isTool ? (
                  <span className="shrink-0 px-1.5 rounded border text-[10px] leading-5 font-semibold bg-blue-500/15 text-blue-300 border-blue-500/30 tabular-nums">
                    iter {r.iter}
                  </span>
                ) : r.lanes.includes("skill") ? (
                  <span className="shrink-0 px-1.5 rounded border text-[10px] leading-5 font-semibold bg-green-500/15 text-green-300 border-green-500/30">
                    skill
                  </span>
                ) : r.lanes.includes("context") ? (
                  <span className="shrink-0 px-1.5 rounded border text-[10px] leading-5 font-semibold bg-purple-500/15 text-purple-300 border-purple-500/30">
                    ctx
                  </span>
                ) : (
                  <span className="shrink-0 w-[44px]" />
                )}
                <span className="shrink-0">{r.icon}</span>
                <span className="shrink-0">{agentIcon(r.e.agent_name || "Master Agent")}</span>
                <div className="min-w-0">
                  <span className={cn("font-medium font-sans", r.milestone ? "text-white" : "text-gray-300")}>
                    {shortName(r.e.agent_name)}
                  </span>
                  <span className="text-gray-400 font-sans"> — {r.text}</span>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
