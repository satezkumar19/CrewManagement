"use client";
import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import toast, { Toaster } from "react-hot-toast";
import {
  Ship, Users, Activity, BarChart3, Radio, Bell,
  Wifi, WifiOff, Anchor, Navigation, RefreshCw, Database, Share2
} from "lucide-react";

import { useWorkflowStore } from "@/store/workflowStore";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useCrew } from "@/hooks/useCrew";
import { workflowApi } from "@/lib/api";
import SignOffTab from "@/components/dashboard/SignOffTab";
import SignOnTab from "@/components/dashboard/SignOnTab";
import SignOnOutcomeCard from "@/components/dashboard/SignOnOutcomeCard";
import ComplianceGraph from "@/components/compliance/ComplianceGraph";
import AgentOrchestrationPanel from "@/components/agents/AgentOrchestrationPanel";
import WorkflowTimeline from "@/components/workflow/WorkflowTimeline";

export default function DashboardPage() {
  const {
    activeTab, setActiveTab,
    signOnCrew, signOffCrew,
    setSignOnCrew, setSignOffCrew,
    activeWorkflow, events,
  } = useWorkflowStore();

  const { isConnected } = useWebSocket();
  const [initiatingSignOff, setInitiatingSignOff] = useState<string | null>(null);
  const lastCrewRefreshKey = useRef<string>("");

  // Step 4: SWR is the fetch/cache layer (dedup, stale-while-revalidate,
  // revalidate-on-focus). We mirror its data into the Zustand store so the tabs
  // and counts — which read the store — keep working unchanged.
  const {
    signOnCrew: swrSignOn, signOffCrew: swrSignOff,
    isLoading: loading, isValidating: crewValidating, error: crewError, refresh,
  } = useCrew();

  useEffect(() => {
    setSignOnCrew(swrSignOn);
    setSignOffCrew(swrSignOff);
  }, [swrSignOn, swrSignOff, setSignOnCrew, setSignOffCrew]);

  // Surface fetch failures (deduped by id so background revalidation can't spam).
  useEffect(() => {
    if (crewError) toast.error("Failed to load crew data", { id: "crew-load-error" });
  }, [crewError]);

  // Re-validate crew when a workflow changes a crew member — e.g. a matched
  // candidate clears compliance and is signed on — so the Sign-Off / Sign-On
  // tabs reflect it live without a manual reload. SWR dedups + revalidates.
  useEffect(() => {
    const latest = events[0];
    if (!latest) return;
    const triggers = ["crew_signed_on", "crew_updated", "workflow_completed"];
    const key = `${latest.event_type}:${latest.timestamp}`;
    if (triggers.includes(latest.event_type) && key !== lastCrewRefreshKey.current) {
      lastCrewRefreshKey.current = key;
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

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
            <NavLink href="/graph" icon={<Share2 className="w-4 h-4" />} label="Graph" />
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
          {/* Tab switcher + SWR cache state (Step 4) */}
          <div className="flex items-center justify-between gap-3">
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
            <CrewCacheBadge validating={crewValidating} />
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
          <SignOnOutcomeCard />
          <ComplianceGraph />
          <AgentOrchestrationPanel />
          {activeWorkflow && <WorkflowTimeline workflow={activeWorkflow} />}
        </div>
      </div>
    </div>
  );
}

// Step 4 indicator: shows the SWR client-cache state for the crew lists — a teal
// "Cached" dot normally, switching to a spinning "Revalidating…" while SWR fetches
// fresh data in the background (stale-while-revalidate).
function CrewCacheBadge({ validating }: { validating: boolean }) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full glass border border-ocean-border/40 text-xs shrink-0">
      {validating ? (
        <>
          <RefreshCw className="w-3 h-3 text-amber-400 animate-spin" />
          <span className="text-amber-300">Revalidating…</span>
        </>
      ) : (
        <>
          <Database className="w-3 h-3 text-teal-400" />
          <span className="text-teal-300">Cached · SWR</span>
        </>
      )}
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
