// Shared L3 Intelligence-match runners.
//
// Both the Sign-Off tab (analyse a departing crew member) and the Intelligence panel
// (ad-hoc rank/port) drive the same pipeline: start the run → call the supervisor →
// store the result → the agent signs on the rank-1 pick → land on the Shortlist tab.
import toast from "react-hot-toast";

import { useWorkflowStore } from "@/store/workflowStore";
import { intelligenceApi } from "@/lib/api";
import type { CrewMember, IntelResult } from "@/types";

// Options that let a caller run L3 alongside another flow (e.g. the managed-agents
// workflow) without L3 stealing the foreground or pre-empting a human sign-on.
export interface RunIntelOptions {
  // false → the agent does NOT auto-sign-on #1 (the managed workflow's human-confirmed
  // sign-on owns that). Default true (standalone L3 behaviour).
  autoSignOn?: boolean;
  // false → don't switch to the Shortlist tab (let the agents' Sign-On view stay).
  // Default true.
  switchToShortlist?: boolean;
}

// After a match resolves: persist it, and (when matched + allowed) have the agent sign on #1.
async function finalize(result: IntelResult, opts: RunIntelOptions = {}): Promise<void> {
  const { autoSignOn = true, switchToShortlist = true } = opts;
  const s = useWorkflowStore.getState();
  s.setIntelResult(result); // authoritative; the live trace already arrived via WS

  if (result.status === "no_crew_found") {
    if (switchToShortlist) s.setActiveTab("shortlist");
    return;
  }

  const top = result.candidates.find((c) => c.rank_position === 1) ?? result.candidates[0];
  if (result.status !== "matched" || !top) return;

  if (switchToShortlist) s.setActiveTab("shortlist");
  if (!autoSignOn) return; // the managed workflow owns the sign-on in the combined flow

  s.setIntelSigningOn(true);
  try {
    await intelligenceApi.signOn(
      top.crew_id,
      top.score,
      top.rationale?.[0],
      (result.context as { vessel?: string })?.vessel,
      result.workflow_id ?? undefined,
    );
    s.setIntelSignedOn(top.crew_id);
    toast.success(`Agent signed on ${top.name} (#1)`);
  } catch (err: unknown) {
    s.setIntelSigningOn(false);
    toast.error(err instanceof Error ? err.message : "Auto sign-on failed");
  }
}

function fail(err: unknown): void {
  const msg = err instanceof Error ? err.message : "Intelligence match failed";
  toast.error(msg);
  useWorkflowStore.getState().setIntelResult({
    status: "error", context: {}, candidates: [], notifications: [], reports: [],
    message: msg, pool_size: 0, disqualified: 0,
    timing: { first_event_ms: 0, total_ms: 0 },
  });
}

/**
 * Analyse a signed-off crew member and shortlist replacements (L3 `match` by crew_id).
 * The supervisor derives the vacancy (rank, grade, vessel, port) from THIS person's
 * record, then delegates to the three investigators.
 */
export async function runIntelByCrew(crew: CrewMember, opts: RunIntelOptions = {}): Promise<void> {
  const s = useWorkflowStore.getState();
  s.startIntelRun(crew.rank, crew.port, {
    crewId: crew.crew_id, name: crew.name, rank: crew.rank, vessel: crew.vessel, port: crew.port,
  });
  try {
    const result = await intelligenceApi.match(crew.crew_id, 3);
    await finalize(result, opts);
  } catch (err) {
    fail(err);
  }
}

/** Ad-hoc match for an explicit vacancy (rank + port), no departing crew member. */
export async function runIntelByContext(rank: string, port?: string, opts: RunIntelOptions = {}): Promise<void> {
  const s = useWorkflowStore.getState();
  s.startIntelRun(rank, port, null);
  try {
    const result = await intelligenceApi.matchContext(rank, port, 3);
    await finalize(result, opts);
  } catch (err) {
    fail(err);
  }
}
