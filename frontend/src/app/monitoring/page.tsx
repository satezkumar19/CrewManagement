"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Anchor, ArrowLeft, RefreshCw, TrendingUp, Clock,
  DollarSign, Target, Zap, Users, CheckCircle, Activity, WifiOff, Database
} from "lucide-react";
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from "recharts";

import { monitoringApi } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import type { SystemMetrics, ROIMetrics } from "@/types";
import { cn, formatDuration, formatCost, agentIcon } from "@/lib/utils";

export default function MonitoringPage() {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [roi, setROI] = useState<ROIMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  useWebSocket();

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      const [m, r] = await Promise.all([
        monitoringApi.getMetrics(),
        monitoringApi.getROI(),
      ]);
      setMetrics(m);
      setROI(r);
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setLoading(false);
    }
  };

  // Build chart data from agent metrics
  const agentBarData = metrics
    ? Object.entries(metrics.agent_metrics).map(([name, m]) => ({
        name: name.replace(" Agent", "").replace(" Matching", " Match"),
        tokens: m.total_tokens,
        cost: parseFloat((m.total_cost * 1000).toFixed(3)),
        runs: m.total_runs,
        duration: Math.round(m.avg_duration_ms / 1000),
      }))
    : [];

  const radarData = metrics
    ? [
        { metric: "Success Rate", value: metrics.success_rate },
        { metric: "Match Accuracy", value: roi?.crew_match_accuracy_percent || 0 },
        { metric: "Compliance Acc.", value: roi?.compliance_accuracy_percent || 0 },
        { metric: "Speed Score", value: metrics.avg_workflow_duration_ms < 60000 ? 90 : 60 },
        { metric: "Cost Efficiency", value: 85 },
      ]
    : [];

  const pieData = metrics
    ? [
        { name: "Completed", value: metrics.completed_workflows, color: "#22c55e" },
        { name: "Active", value: metrics.active_workflows, color: "#3b82f6" },
        { name: "Failed", value: metrics.failed_workflows, color: "#ef4444" },
      ].filter(d => d.value > 0)
    : [];

  const mockTimeSeries = Array.from({ length: 12 }, (_, i) => ({
    time: `${String(i * 2).padStart(2, "0")}:00`,
    workflows: Math.floor(Math.random() * 5),
    tokens: Math.floor(Math.random() * 50000),
    cost: parseFloat((Math.random() * 0.5).toFixed(3)),
  }));

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
              <p className="text-xs text-gray-500">Monitoring & ROI Dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/" className="flex items-center gap-2 px-4 py-2 text-gray-400 hover:text-white text-sm">
              <ArrowLeft className="w-4 h-4" /> Dashboard
            </Link>
            <Link href="/workflow" className="px-4 py-2 text-gray-400 hover:text-white text-sm">Workflow</Link>
            <Link href="/decisions" className="px-4 py-2 text-gray-400 hover:text-white text-sm">Decisions</Link>
            <Link href="/graph" className="px-4 py-2 text-gray-400 hover:text-white text-sm">Graph</Link>
            <button onClick={loadData} className="p-2 text-gray-400 hover:text-white">
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </nav>

      <div className="max-w-screen-2xl mx-auto px-6 py-8 space-y-6">
        {offline && (
          <div className="glass rounded-2xl p-6 border border-red-500/30 bg-red-900/10 flex items-center gap-4">
            <WifiOff className="w-8 h-8 text-red-400 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-red-300">Backend Unreachable</p>
              <p className="text-xs text-red-400/70 mt-0.5">
                Start the FastAPI backend on <span className="font-mono">http://localhost:8000</span> then click refresh.
              </p>
            </div>
            <button
              onClick={loadData}
              className="ml-auto flex items-center gap-2 px-4 py-2 rounded-xl bg-red-500/20 text-red-300 border border-red-500/30 hover:bg-red-500/30 text-sm"
            >
              <RefreshCw className="w-4 h-4" /> Retry
            </button>
          </div>
        )}
        {loading ? (
          <div className="flex justify-center py-32">
            <div className="w-12 h-12 border-4 border-ocean-accent/30 border-t-ocean-accent rounded-full animate-spin" />
          </div>
        ) : offline ? null : (
          <>
            {/* ── KPI Cards ──────────────────────────────────────────────────── */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
              <KPICard
                icon={<Activity className="w-5 h-5" />}
                label="Total Workflows"
                value={String(metrics?.total_workflows || 0)}
                sub={`${metrics?.active_workflows || 0} active`}
                color="text-ocean-accent"
              />
              <KPICard
                icon={<CheckCircle className="w-5 h-5" />}
                label="Success Rate"
                value={`${metrics?.success_rate || 0}%`}
                sub={`${metrics?.completed_workflows || 0} completed`}
                color="text-green-400"
              />
              <KPICard
                icon={<Zap className="w-5 h-5" />}
                label="Total Tokens"
                value={(metrics?.total_tokens || 0).toLocaleString()}
                sub={`$${(metrics?.total_cost || 0).toFixed(4)} spent`}
                color="text-purple-400"
              />
              <KPICard
                icon={<Database className="w-5 h-5" />}
                label="Cache Hit Rate"
                value={`${metrics?.cache_hit_rate ?? 0}%`}
                sub={`${(metrics?.cache_read_tokens || 0).toLocaleString()} tokens from cache`}
                color="text-teal-400"
              />
              <KPICard
                icon={<Clock className="w-5 h-5" />}
                label="Avg Duration"
                value={formatDuration(metrics?.avg_workflow_duration_ms || 0)}
                sub="per workflow"
                color="text-blue-400"
              />
            </div>

            {/* ── ROI Cards ──────────────────────────────────────────────────── */}
            {roi && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KPICard
                  icon={<TrendingUp className="w-5 h-5" />}
                  label="Time Saved / Op"
                  value={`${roi.time_saved_per_operation_hours}h`}
                  sub="vs manual 8hr process"
                  color="text-green-400"
                  accent
                />
                <KPICard
                  icon={<DollarSign className="w-5 h-5" />}
                  label="Savings / Op"
                  value={`$${roi.automation_savings_usd_per_op.toFixed(0)}`}
                  sub={`vs $${roi.manual_cost_estimate_usd} manual`}
                  color="text-yellow-400"
                  accent
                />
                <KPICard
                  icon={<Target className="w-5 h-5" />}
                  label="Match Accuracy"
                  value={`${roi.crew_match_accuracy_percent.toFixed(1)}%`}
                  sub="crew matching AI"
                  color="text-purple-400"
                  accent
                />
                <KPICard
                  icon={<Users className="w-5 h-5" />}
                  label="Compliance Acc."
                  value={`${roi.compliance_accuracy_percent.toFixed(1)}%`}
                  sub="document validation"
                  color="text-ocean-accent"
                  accent
                />
              </div>
            )}

            {/* ── Charts Row 1 ───────────────────────────────────────────────── */}
            <div className="grid grid-cols-12 gap-6">
              {/* Area chart - workflow activity over time */}
              <div className="col-span-12 lg:col-span-8 glass rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-white mb-4">Workflow Activity (Mock 24h)</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={mockTimeSeries}>
                    <defs>
                      <linearGradient id="colorWorkflows" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#00d4ff" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" />
                    <XAxis dataKey="time" stroke="#4b5563" tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <YAxis stroke="#4b5563" tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ background: "#0d1f3c", border: "1px solid #1e3a5f", borderRadius: 8 }}
                      labelStyle={{ color: "#e2e8f0" }}
                    />
                    <Area type="monotone" dataKey="workflows" stroke="#00d4ff" fill="url(#colorWorkflows)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Pie chart - workflow status */}
              <div className="col-span-12 lg:col-span-4 glass rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-white mb-4">Workflow Status</h3>
                {pieData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={180}>
                    <PieChart>
                      <Pie data={pieData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">
                        {pieData.map((entry, i) => (
                          <Cell key={i} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={{ background: "#0d1f3c", border: "1px solid #1e3a5f", borderRadius: 8 }} />
                      <Legend iconType="circle" wrapperStyle={{ fontSize: 12, color: "#9ca3af" }} />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
                    Run workflows to see data
                  </div>
                )}
              </div>
            </div>

            {/* ── Charts Row 2 ───────────────────────────────────────────────── */}
            <div className="grid grid-cols-12 gap-6">
              {/* Bar chart - agent token usage */}
              <div className="col-span-12 lg:col-span-7 glass rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-white mb-4">Agent Performance Metrics</h3>
                {agentBarData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={agentBarData} barCategoryGap="20%">
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" />
                      <XAxis dataKey="name" stroke="#4b5563" tick={{ fill: "#6b7280", fontSize: 10 }} />
                      <YAxis stroke="#4b5563" tick={{ fill: "#6b7280", fontSize: 11 }} />
                      <Tooltip contentStyle={{ background: "#0d1f3c", border: "1px solid #1e3a5f", borderRadius: 8 }} />
                      <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
                      <Bar dataKey="tokens" name="Tokens" fill="#00d4ff" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="duration" name="Avg Duration (s)" fill="#a855f7" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
                    Run workflows to see agent metrics
                  </div>
                )}
              </div>

              {/* Radar chart - system quality */}
              <div className="col-span-12 lg:col-span-5 glass rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-white mb-4">System Quality Radar</h3>
                {radarData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={220}>
                    <RadarChart data={radarData}>
                      <PolarGrid stroke="#1e3a5f" />
                      <PolarAngleAxis dataKey="metric" tick={{ fill: "#6b7280", fontSize: 10 }} />
                      <Radar dataKey="value" stroke="#00d4ff" fill="#00d4ff" fillOpacity={0.2} />
                      <Tooltip contentStyle={{ background: "#0d1f3c", border: "1px solid #1e3a5f", borderRadius: 8 }} />
                    </RadarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
                    Run workflows to see quality metrics
                  </div>
                )}
              </div>
            </div>

            {/* ── Agent Detail Cards ─────────────────────────────────────────── */}
            {metrics && Object.keys(metrics.agent_metrics).length > 0 && (
              <div className="glass rounded-2xl overflow-hidden">
                <div className="px-6 py-4 border-b border-ocean-border">
                  <h3 className="text-sm font-semibold text-white">Agent-Level Metrics</h3>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 p-6">
                  {Object.entries(metrics.agent_metrics).map(([name, m]) => (
                    <motion.div
                      key={name}
                      whileHover={{ scale: 1.02 }}
                      className="rounded-xl bg-ocean/40 border border-ocean-border/40 p-4 hover:border-ocean-accent/30 transition-all"
                    >
                      <div className="text-2xl mb-2">{agentIcon(name)}</div>
                      <div className="text-xs font-semibold text-white leading-tight mb-2">
                        {name.replace(" Agent", "")}
                      </div>
                      <div className="space-y-1">
                        <MetricRow label="Runs" value={String(m.total_runs)} />
                        <MetricRow label="Success" value={`${m.completed}/${m.total_runs}`} />
                        <MetricRow label="Tokens" value={m.total_tokens.toLocaleString()} />
                        <MetricRow label="Cost" value={formatCost(m.total_cost)} />
                        <MetricRow label="Avg Time" value={formatDuration(m.avg_duration_ms)} />
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}

            {/* ── Backend Cache (Steps 2 & 3) ───────────────────────────────── */}
            {metrics?.backend_cache && (
              <div className="glass rounded-2xl p-6">
                <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                  <Database className="w-4 h-4 text-teal-400" /> Backend Cache
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Step 2 — in-process lru_cache */}
                  <div className="rounded-xl bg-ocean/40 border border-ocean-border/40 p-4">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-xs font-semibold text-gray-300">In-Process · lru_cache</span>
                      <span className="text-lg font-bold text-teal-400">{metrics.backend_cache.lru.hit_rate}%</span>
                    </div>
                    <div className="space-y-1">
                      <MetricRow label="Hits" value={metrics.backend_cache.lru.hits.toLocaleString()} />
                      <MetricRow label="Misses" value={metrics.backend_cache.lru.misses.toLocaleString()} />
                      {Object.entries(metrics.backend_cache.lru.by_cache).map(([k, c]) => (
                        <MetricRow key={k} label={k} value={`${c.hits} / ${c.hits + c.misses}`} />
                      ))}
                    </div>
                  </div>
                  {/* Step 3 — Redis crew cache */}
                  <div className="rounded-xl bg-ocean/40 border border-ocean-border/40 p-4">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-xs font-semibold text-gray-300">Redis Crew Cache</span>
                      <span className="text-lg font-bold text-teal-400">{metrics.backend_cache.redis_crew.hit_rate}%</span>
                    </div>
                    <div className="space-y-1">
                      <MetricRow label="Hits" value={metrics.backend_cache.redis_crew.hits.toLocaleString()} />
                      <MetricRow label="Misses" value={metrics.backend_cache.redis_crew.misses.toLocaleString()} />
                      <MetricRow label="Errors (→ DB fallback)" value={metrics.backend_cache.redis_crew.errors.toLocaleString()} />
                      <MetricRow
                        label="Status"
                        value={metrics.backend_cache.redis_crew.available ? "available" : "disabled"}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* ── System Health ─────────────────────────────────────────────── */}
            <div className="glass rounded-2xl p-6">
              <h3 className="text-sm font-semibold text-white mb-4">System Health</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                <HealthMetric
                  label="Active Agents" value={5} max={5}
                  color="bg-green-400" sub="All 5 agents online"
                />
                <HealthMetric
                  label="Active Workflows" value={metrics?.active_workflows || 0} max={10}
                  color="bg-blue-400" sub={`${metrics?.active_workflows || 0} running`}
                />
                <HealthMetric
                  label="WS Connections" value={metrics?.active_websocket_connections || 0} max={20}
                  color="bg-purple-400" sub="WebSocket clients"
                />
                <HealthMetric
                  label="Failed Workflows" value={metrics?.failed_workflows || 0} max={metrics?.total_workflows || 1}
                  color="bg-red-400" sub={`${100 - (metrics?.success_rate || 100)}% failure rate`}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function KPICard({
  icon, label, value, sub, color, accent = false
}: {
  icon: React.ReactNode; label: string; value: string; sub: string; color: string; accent?: boolean;
}) {
  return (
    <motion.div
      whileHover={{ scale: 1.02 }}
      className={cn(
        "glass rounded-2xl p-5 border transition-all",
        accent ? "border-ocean-accent/30 bg-ocean-accent/5" : "border-ocean-border/40"
      )}
    >
      <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center mb-3", color, "bg-current/10")}>
        <span className={color}>{icon}</span>
      </div>
      <div className="text-2xl font-bold text-white">{value}</div>
      <div className="text-xs font-medium text-gray-300 mt-0.5">{label}</div>
      <div className="text-xs text-gray-600 mt-0.5">{sub}</div>
    </motion.div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-xs text-gray-600">{label}</span>
      <span className="text-xs text-gray-300">{value}</span>
    </div>
  );
}

function HealthMetric({
  label, value, max, color, sub
}: {
  label: string; value: number; max: number; color: string; sub: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div>
      <div className="flex justify-between mb-1.5">
        <span className="text-xs text-gray-400">{label}</span>
        <span className="text-xs font-medium text-white">{value}/{max}</span>
      </div>
      <div className="w-full bg-gray-700/50 rounded-full h-2">
        <motion.div
          className={cn("h-2 rounded-full", color)}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: "easeOut" }}
        />
      </div>
      <p className="text-xs text-gray-600 mt-1">{sub}</p>
    </div>
  );
}
