"use client";
import { motion, AnimatePresence } from "framer-motion";
import { Bot, Loader2, Users, Ship, FileText, Network } from "lucide-react";
import { useWorkflowStore } from "@/store/workflowStore";

// Pick an icon/colour by which investigator (or the coordinator) spoke.
function agentStyle(name: string): { icon: typeof Bot; color: string } {
  const n = (name || "").toLowerCase();
  if (n.includes("crew")) return { icon: Users, color: "text-sky-300" };
  if (n.includes("contract") || n.includes("wage")) return { icon: FileText, color: "text-amber-300" };
  if (n.includes("vessel")) return { icon: Ship, color: "text-violet-300" };
  return { icon: Network, color: "text-ocean-accent" }; // coordinator / supervisor
}

/**
 * Live feed of the Managed-Agents reasoning. The deterministic shortlist is shown
 * instantly (meets the <10s SLO); the real LLM coordinator + 3 sub-agents then stream
 * their narrative here behind it. Renders nothing unless a managed run is active or has
 * produced messages.
 */
export default function AgentNarration() {
  const { intel } = useWorkflowStore();
  const { agentNarration, narrating } = intel;

  if (!narrating && agentNarration.length === 0) return null;

  return (
    <div className="rounded-lg bg-ocean-card/50 border border-ocean-border/50 p-3">
      <div className="flex items-center gap-2 mb-2">
        <Bot className="w-4 h-4 text-ocean-accent" />
        <span className="text-xs font-medium text-white">Managed agents · live reasoning</span>
        {narrating ? (
          <span className="flex items-center gap-1 text-[10px] text-ocean-accent">
            <Loader2 className="w-3 h-3 animate-spin" /> reasoning…
          </span>
        ) : (
          <span className="text-[10px] text-emerald-400">complete</span>
        )}
        <span className="ml-auto text-[9px] text-gray-600">
          deterministic result already shown — this enriches it
        </span>
      </div>

      <div className="space-y-2 max-h-64 overflow-y-auto">
        <AnimatePresence initial={false}>
          {agentNarration.map((m, i) => {
            const { icon: Icon, color } = agentStyle(m.agent);
            return (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-start gap-2"
              >
                <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${color}`} />
                <div className="min-w-0">
                  <p className={`text-[10px] font-medium ${color}`}>{m.agent}</p>
                  <p className="text-[11px] text-gray-300 leading-snug">{m.text}</p>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {narrating && agentNarration.length === 0 && (
          <p className="text-[11px] text-gray-500">
            Coordinator is delegating to the Crew, Contract/Wage, and Vessel Ops investigators…
          </p>
        )}
      </div>
    </div>
  );
}
