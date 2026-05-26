"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import toast, { Toaster } from "react-hot-toast";
import {
  Ship, Users, Activity, BarChart3, Radio, Bell,
  Wifi, WifiOff, Anchor, Navigation
} from "lucide-react";

import { useWorkflowStore } from "@/store/workflowStore";
import { useWebSocket } from "@/hooks/useWebSocket";
import { crewApi, workflowApi } from "@/lib/api";
import SignOffTab from "@/components/dashboard/SignOffTab";
import SignOnTab from "@/components/dashboard/SignOnTab";
import AgentOrchestrationPanel from "@/components/agents/AgentOrchestrationPanel";
import WorkflowTimeline from "@/components/workflow/WorkflowTimeline";

export default function DashboardPage() {
  const {
    activeTab, setActiveTab,
    signOnCrew, signOffCrew,
    setSignOnCrew, setSignOffCrew,
    activeWorkflow, events,
    setActiveWorkflow, setMatchedCandidate,
  } = useWorkflowStore();

  const { isConnected } = useWebSocket();
  const [loading, setLoading] = useState(true);
  const [initiatingSignOff, setInitiatingSignOff] = useState<string | null>(null);

  useEffect(() => {
    void loadCrew();
    void hydrateActiveWorkflow();
    // We deliberately depend on neither store setters nor hydrateActiveWorkflow:
    // this runs once on mount, and store actions are referentially stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadCrew = async () => {
    try {
      const [sOn, sOff] = await Promise.all([
        crewApi.getSignOnCrew(),
        crewApi.getSignOffCrew(),
      ]);
      setSignOnCrew(sOn);
      setSignOffCrew(sOff);
    } catch (err) {
      toast.error("Failed to load crew data");
    } finally {
      setLoading(false);
    }
  };

  // Restore the most recent in-flight workflow into the store after a page
  // reload. Without this the Sign On buttons and the WorkflowTimeline panel
  // (which carries the compliance-retry banner) wouldn't render until the
  // operator manually initiates a new sign-off.
  const hydrateActiveWorkflow = async () => {
    try {
      const all = await workflowApi.listWorkflows(20);
      const nonTerminal = all.find((w) =>
        ["pending", "running", "waiting", "retrying_compliance", "paused"].includes(w.status)
      );
      if (nonTerminal) {
        setActiveWorkflow(nonTerminal);
        if (nonTerminal.matched_crew_id) {
          setMatchedCandidate(nonTerminal.matched_crew_id);
        }
        useWorkflowStore.getState().setShowWorkflowPanel(true);
        // If the workflow is past Phase 1, drop the operator straight into
        // the Sign On tab so the matched candidate is visible.
        if (["waiting", "retrying_compliance", "running"].includes(nonTerminal.status)) {
          setActiveTab("sign-on");
        }
      }
    } catch {
      // Best-effort hydration — fall back to empty state on any failure.
    }
  };

  const handleInitiateSignOff = async (crewId: string, crewName: string) => {
    setInitiatingSignOff(crewId);
    try {
      const result = await workflowApi.initiateSignOff(crewId, "Contract completion");
      toast.success(`Sign-off initiated for ${crewName}`);
      useWorkflowStore.getState().setShowWorkflowPanel(true);
      setActiveTab("sign-on");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to initiate sign-off";
      toast.error(message);
    } finally {
      setInitiatingSignOff(null);
    }
  };

  const handleSignOn = async (candidateId: string, candidateName: string) => {
    if (!activeWorkflow?.workflow_id) {
      toast.error("No active workflow found");
      return;
    }
    try {
      await workflowApi.initiateSignOn(activeWorkflow.workflow_id, candidateId);
      toast.success(`Compliance check initiated for ${candidateName}`);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to initiate sign-on";
      toast.error(message);
    }
  };

  return (
    <div className="min-h-screen bg-ocean-gradient">
      <Toaster position="top-right" toastOptions={{
        style: { background: "#0d1f3c", color: "#e2e8f0", border: "1px solid #1e3a5f" }
      }} />

      {/* ── Navigation Bar ─────────────────────────────────────────────────── */}
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
            <NavLink href="/" icon={<Ship className="w-4 h-4" />} label="Dashboard" active />
            <NavLink href="/workflow" icon={<Activity className="w-4 h-4" />} label="Workflow" />
            <NavLink href="/monitoring" icon={<BarChart3 className="w-4 h-4" />} label="Monitoring" />
          </div>

          <div className="flex items-center gap-4">
            {/* WebSocket status */}
            <div className="flex items-center gap-2 text-xs">
              {isConnected ? (
                <><Wifi className="w-3 h-3 text-green-400" /><span className="text-green-400">Live</span></>
              ) : (
                <><WifiOff className="w-3 h-3 text-red-400" /><span className="text-red-400">Offline</span></>
              )}
            </div>

            {/* Workflow status indicator */}
            {activeWorkflow && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-blue-500/20 border border-blue-500/30"
              >
                <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                <span className="text-xs text-blue-300">Workflow Active</span>
              </motion.div>
            )}

            {/* Event counter */}
            {events.length > 0 && (
              <div className="relative">
                <Bell className="w-5 h-5 text-gray-400" />
                <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-ocean-accent text-white text-xs flex items-center justify-center">
                  {Math.min(events.length, 9)}
                </span>
              </div>
            )}

            {/* Live agent indicator */}
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <Radio className="w-3 h-3" />
              <span>5 Agents Ready</span>
            </div>
          </div>
        </div>
      </nav>

      {/* ── Main Layout ────────────────────────────────────────────────────── */}
      <div className="max-w-screen-2xl mx-auto px-6 py-6 grid grid-cols-12 gap-6">

        {/* ── Left: Crew Panel (8 cols) ─────────────────────────────────────── */}
        <div className="col-span-12 xl:col-span-8 space-y-4">
          {/* Tab switcher */}
          <div className="glass rounded-2xl p-1 flex gap-1 w-fit">
            {(["sign-off", "sign-on"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-6 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 ${
                  activeTab === tab
                    ? "bg-accent-gradient text-white shadow-lg"
                    : "text-gray-400 hover:text-white hover:bg-ocean-border/30"
                }`}
              >
                {tab === "sign-off" ? (
                  <span className="flex items-center gap-2">
                    <Ship className="w-4 h-4" /> Sign Off
                    <span className="bg-white/20 rounded-full px-1.5 text-xs">{signOffCrew.length}</span>
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <Users className="w-4 h-4" /> Sign On
                    <span className="bg-white/20 rounded-full px-1.5 text-xs">{signOnCrew.length}</span>
                  </span>
                )}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            {loading ? (
              <motion.div
                key="loading"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="glass rounded-2xl p-20 flex flex-col items-center gap-4"
              >
                <div className="w-12 h-12 border-4 border-ocean-accent/30 border-t-ocean-accent rounded-full animate-spin" />
                <p className="text-gray-400">Loading crew data...</p>
              </motion.div>
            ) : activeTab === "sign-off" ? (
              <motion.div
                key="signoff"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                transition={{ duration: 0.3 }}
              >
                <SignOffTab
                  crew={signOffCrew}
                  initiatingId={initiatingSignOff}
                  onInitiateSignOff={handleInitiateSignOff}
                />
              </motion.div>
            ) : (
              <motion.div
                key="signon"
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={{ duration: 0.3 }}
              >
                <SignOnTab crew={signOnCrew} onSignOn={handleSignOn} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* ── Right: Agent Panel (4 cols) ───────────────────────────────────── */}
        <div className="col-span-12 xl:col-span-4 space-y-4">
          <AgentOrchestrationPanel />
          {activeWorkflow && <WorkflowTimeline workflow={activeWorkflow} />}
        </div>
      </div>
    </div>
  );
}

function NavLink({
  href, icon, label, active = false
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
