import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

export function formatCost(usd: number): string {
  if (usd < 0.001) return `$${(usd * 1000).toFixed(3)}m`;
  return `$${usd.toFixed(4)}`;
}

export function statusColor(status: string): string {
  const map: Record<string, string> = {
    idle: "text-gray-400",
    pending: "text-yellow-400",
    running: "text-blue-400",
    waiting: "text-orange-400",
    completed: "text-green-400",
    failed: "text-red-400",
    cancelled: "text-gray-500",
    paused: "text-purple-400",
    retrying_compliance: "text-orange-400",
    Valid: "text-green-400",
    "Expiring Soon": "text-yellow-400",
    Expired: "text-red-400",
    Available: "text-green-400",
    Onboard: "text-blue-400",
    Matched: "text-purple-400",
    passed: "text-green-400",
    warning: "text-yellow-400",
    PASSED: "text-green-400",
    WARNING: "text-yellow-400",
    FAILED: "text-red-400",
  };
  return map[status] || "text-gray-300";
}

export function statusBg(status: string): string {
  const map: Record<string, string> = {
    idle: "bg-gray-500/20 text-gray-300 border-gray-500/30",
    pending: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    running: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    waiting: "bg-orange-500/20 text-orange-300 border-orange-500/30",
    completed: "bg-green-500/20 text-green-300 border-green-500/30",
    failed: "bg-red-500/20 text-red-300 border-red-500/30",
    cancelled: "bg-gray-500/20 text-gray-400 border-gray-500/30",
    paused: "bg-purple-500/20 text-purple-300 border-purple-500/30",
    retrying_compliance: "bg-orange-500/20 text-orange-300 border-orange-500/30",
    Valid: "bg-green-500/20 text-green-300 border-green-500/30",
    "Expiring Soon": "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    Expired: "bg-red-500/20 text-red-300 border-red-500/30",
    Available: "bg-green-500/20 text-green-300 border-green-500/30",
    Onboard: "bg-blue-500/20 text-blue-300 border-blue-500/30",
    Matched: "bg-purple-500/20 text-purple-300 border-purple-500/30",
    passed: "bg-green-500/20 text-green-300 border-green-500/30",
    warning: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    PASSED: "bg-green-500/20 text-green-300 border-green-500/30",
    WARNING: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    FAILED: "bg-red-500/20 text-red-300 border-red-500/30",
  };
  return map[status] || "bg-gray-500/20 text-gray-300 border-gray-500/30";
}

export function agentIcon(agentName: string): string {
  const icons: Record<string, string> = {
    "Master Agent": "🧭",
    "Crew Matching Agent": "👥",
    "Travel Agent": "✈️",
    "Notification Agent": "📧",
    "Compliance Agent": "🛡️",
  };
  return icons[agentName] || "🤖";
}
