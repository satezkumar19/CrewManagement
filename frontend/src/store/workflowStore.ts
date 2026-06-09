import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type {
  WorkflowState,
  CrewMember,
  WSEvent,
  AgentExecution,
  AgentStatus,
  ComplianceSubgraph,
  IntelResult,
  IntelRunState,
  IntelRankedCandidate,
  IntelNotification,
  IntelInvestigatorState,
  IntelFitGraph,
  IntelSubject,
  IntelAgentMessage,
} from "@/types";

// ─── L3 Intelligence helpers ──────────────────────────────────────────────────
const emptyIntelResult = (): IntelResult => ({
  workflow_id: null, status: "matched", context: {}, candidates: [],
  notifications: [], reports: [], message: "", pool_size: 0, disqualified: 0,
  timing: { first_event_ms: 0, total_ms: 0 },
});

// The 3 investigators, in pipeline order, all idle.
const initialInvestigators = (): IntelInvestigatorState[] => [
  { key: "crew", name: "Crew Intel", status: "idle" },
  { key: "contract", name: "Contract/Wage Intel", status: "idle" },
  { key: "vessel", name: "Vessel Ops Intel", status: "idle" },
];

const investigatorKey = (name: string): IntelInvestigatorState["key"] | null => {
  const n = (name || "").toLowerCase();
  if (n.includes("crew")) return "crew";
  if (n.includes("contract") || n.includes("wage")) return "contract";
  if (n.includes("vessel")) return "vessel";
  return null;
};

// Apply an investigator status/eligibility update immutably.
const patchInvestigator = (
  list: IntelInvestigatorState[],
  name: string,
  patch: Partial<IntelInvestigatorState>,
): IntelInvestigatorState[] => {
  const key = investigatorKey(name);
  if (!key) return list;
  return list.map((i) => (i.key === key ? { ...i, ...patch } : i));
};

// Human-readable one-liner for a streamed intel_* event (mirrors the demo trace).
function intelLabel(type: string, d: Record<string, unknown>): string {
  switch (type) {
    case "intel_supervisor_started":
      return `Supervisor delegating → ${((d.investigators as string[]) || []).join(", ")}`;
    case "intel_investigator_started":
      return `${d.investigator} started (pool ${d.pool_size})`;
    case "intel_investigator_completed":
      return `${d.investigator}: eligible ${d.eligible}/${d.assessed}`;
    case "intel_ranking":
      return `Ranked top-${d.top_n} candidates`;
    case "intel_graph":
      return `Fit graph built — ${d.node_count} nodes, ${d.edge_count} edges`;
    case "intel_signed_on":
      return `Agent signed on ${d.name} (#1) → onboard`;
    case "intel_narration_started":
      return `Managed agents reasoning (live)…`;
    case "intel_agent_message":
      return `${d.agent}: ${String(d.text || "").slice(0, 60)}…`;
    case "intel_narration_done":
      return `Agent reasoning complete`;
    case "intel_no_crew":
      return `No eligible crew — ${d.message}`;
    case "intel_notification_sent":
      return `Notified ${d.role} via ${d.channel} [${d.status}]`;
    case "intel_supervisor_completed":
      return `Completed — status ${d.status}, ${d.shortlisted} shortlisted`;
    default:
      return type;
  }
}

interface AgentLiveState {
  name: string;
  status: AgentStatus;
  current_task?: string;
  tokens_used: number;
  estimated_cost: number;
  duration_ms?: number;
  tool_calls: number;
  last_tool?: string;
  confidence_score?: number;
  // Skill usage during the run. `skills_loaded` holds a dedup key per distinct
  // skill access (resolved name when known, else the access input) so reloads of
  // one skill count once; `skills_used` is its length.
  skills_used: number;
  skills_loaded: string[];
  last_skill?: string;
}

export interface SignOnOutcome {
  crewId?: string;
  crewName?: string;
  crewRank?: string;
  matchConfidence?: number;
  phase: "validating" | "signed_on" | "rejected";
  complianceStatus?: string;
  complianceScore?: number;
  reasons?: string[]; // warnings (conditional) or failures (rejected)
  recommendation?: string;
  subgraph?: ComplianceSubgraph; // compliance context graph the agent reasoned over
}

interface WorkflowStore {
  // Crew data
  signOnCrew: CrewMember[];
  signOffCrew: CrewMember[];
  matchedCandidateId: string | null;
  setSignOnCrew: (crew: CrewMember[]) => void;
  setSignOffCrew: (crew: CrewMember[]) => void;
  setMatchedCandidate: (id: string | null) => void;

  // Workflow
  activeWorkflow: WorkflowState | null;
  workflowHistory: WorkflowState[];
  setActiveWorkflow: (w: WorkflowState | null) => void;
  updateActiveWorkflow: (partial: Partial<WorkflowState>) => void;
  addToHistory: (w: WorkflowState) => void;

  // Live agent states
  agentStates: Record<string, AgentLiveState>;
  updateAgentState: (name: string, state: Partial<AgentLiveState>) => void;
  resetAgentStates: () => void;

  // Events log
  events: WSEvent[];
  addEvent: (e: WSEvent) => void;
  clearEvents: () => void;

  // The latest DECISION-relevant event only (decision_logged / decision_outcome /
  // precedent_consulted). The Decisions page subscribes to this instead of the full
  // `events` array so a live run's flood of agent/tool events doesn't re-render it.
  lastDecisionEvent: WSEvent | null;

  // decision_ids surfaced by a LIVE sign-off this session (from decision_logged).
  // The Decisions page shows only these live rows — not every decision already
  // persisted in the DB — so the tab starts empty until a sign-off or a seed.
  liveDecisionIds: string[];

  // WebSocket connection status, driven by the singleton socket so it updates
  // reactively (the old per-page hook read a ref that never triggered a re-render).
  wsConnected: boolean;
  setWsConnected: (v: boolean) => void;

  // Sign-on outcome for the active workflow: which crew was matched + the
  // compliance verdict (signed on / rejected + reasons).
  signOnOutcome: SignOnOutcome | null;

  // L3 Intelligence Graph run (driven by intel_* WS events)
  intel: IntelRunState;
  startIntelRun: (vacatedRank: string, port?: string, subject?: IntelSubject | null) => void;
  setIntelResult: (result: IntelResult) => void;
  setIntelSigningOn: (b: boolean) => void;
  setIntelSignedOn: (crewId: string) => void;

  // WebSocket event handler
  handleWSEvent: (event: WSEvent) => void;

  // UI state
  activeTab: "sign-on" | "sign-off" | "shortlist";
  setActiveTab: (tab: "sign-on" | "sign-off" | "shortlist") => void;
  showWorkflowPanel: boolean;
  setShowWorkflowPanel: (v: boolean) => void;
}

const mkAgent = (name: string): AgentLiveState => ({
  name, status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0,
  skills_used: 0, skills_loaded: [],
});
const DEFAULT_AGENT_STATES: Record<string, AgentLiveState> = {
  "Master Agent": mkAgent("Master Agent"),
  "Crew Matching Agent": mkAgent("Crew Matching Agent"),
  "Travel Agent": mkAgent("Travel Agent"),
  "Notification Agent": mkAgent("Notification Agent"),
  "Compliance Agent": mkAgent("Compliance Agent"),
};

export const useWorkflowStore = create<WorkflowStore>()(
  devtools(
    (set, get) => ({
      signOnCrew: [],
      signOffCrew: [],
      matchedCandidateId: null,
      setSignOnCrew: (crew) => set({ signOnCrew: crew }),
      setSignOffCrew: (crew) => set({ signOffCrew: crew }),
      setMatchedCandidate: (id) => set({ matchedCandidateId: id }),

      activeWorkflow: null,
      workflowHistory: [],
      setActiveWorkflow: (w) => set({ activeWorkflow: w }),
      updateActiveWorkflow: (partial) =>
        set((state) => ({
          activeWorkflow: state.activeWorkflow
            ? { ...state.activeWorkflow, ...partial }
            : null,
        })),
      addToHistory: (w) =>
        set((state) => ({
          workflowHistory: [w, ...state.workflowHistory].slice(0, 50),
        })),

      agentStates: { ...DEFAULT_AGENT_STATES },
      updateAgentState: (name, state) =>
        set((s) => ({
          agentStates: {
            ...s.agentStates,
            [name]: { ...s.agentStates[name], ...state },
          },
        })),
      resetAgentStates: () =>
        set({
          agentStates: JSON.parse(JSON.stringify(DEFAULT_AGENT_STATES)),
        }),

      signOnOutcome: null,

      intel: {
        running: false, startedAt: null, trace: [], result: null,
        investigators: initialInvestigators(), fitGraph: null,
        signedOnId: null, signingOn: false, subject: null,
        agentNarration: [], narrating: false,
      },
      startIntelRun: (vacatedRank, port, subject = null) =>
        set({
          intel: {
            running: true, startedAt: Date.now(), trace: [], result: null,
            vacatedRank, port, investigators: initialInvestigators(), fitGraph: null,
            signedOnId: null, signingOn: false, subject,
        agentNarration: [], narrating: false,
          },
        }),
      setIntelResult: (result) =>
        set((s) => ({
          intel: {
            ...s.intel, result, running: false,
            // Final result is authoritative; keep the live graph if the payload omits it.
            fitGraph: result.fit_graph ?? s.intel.fitGraph,
          },
        })),
      setIntelSigningOn: (b) =>
        set((s) => ({ intel: { ...s.intel, signingOn: b } })),
      setIntelSignedOn: (crewId) =>
        set((s) => ({ intel: { ...s.intel, signedOnId: crewId, signingOn: false } })),

      events: [],
      // Keep a full session's worth of events (a multiagent run emits many);
      // the buffer is wiped on the next sign-off (workflow_created), so it stays
      // bounded without dropping logs mid-session.
      addEvent: (e) =>
        set((s) => ({ events: [e, ...s.events].slice(0, 2000) })),
      clearEvents: () => set({ events: [] }),

      lastDecisionEvent: null,
      liveDecisionIds: [],

      wsConnected: false,
      setWsConnected: (v) => set({ wsConnected: v }),

      handleWSEvent: (event) => {
        const { updateAgentState, updateActiveWorkflow, setMatchedCandidate, addEvent } = get();
        addEvent(event);

        // Surface decision-relevant events on a dedicated slot so the Decisions
        // page can react to just these (not the full live-run event stream).
        if (
          event.event_type === "decision_logged" ||
          event.event_type === "decision_outcome" ||
          event.event_type === "precedent_consulted" ||
          // The compliance verdict (incl. the reject→retry journey) — used to drive
          // the Decision Graph's outcome even if the DB outcome-stamp was missed.
          event.event_type === "crew_signed_on" ||
          event.event_type === "sign_on_rejected" ||
          // Intermediate retry-loop events drive the LIVE Decision Graph: each
          // candidate being validated (auto_compliance) and each mid-retry rejection
          // (sign_on_attempt_rejected) updates the in-progress attempt chain so a
          // rejected candidate flips to "Rejected" and the next candidate appears the
          // moment the orchestrator moves on — instead of sitting on "Pending".
          event.event_type === "auto_compliance" ||
          event.event_type === "sign_on_attempt_rejected" ||
          // HITL — the automated process paused for a human (review_requested) and the
          // human's verdict landed (decision_reviewed). Both drive the review queue and
          // the decision's live state on the Decisions tab.
          event.event_type === "review_requested" ||
          event.event_type === "decision_reviewed"
        ) {
          set({ lastDecisionEvent: event });
        }
        // Remember which live decisions were surfaced this session so the Decisions
        // page can show only those (not every row already in the DB).
        if (event.event_type === "decision_logged") {
          const id = (event.data || {}).decision_id as string | undefined;
          if (id && !get().liveDecisionIds.includes(id)) {
            set({ liveDecisionIds: [...get().liveDecisionIds, id] });
          }
        }

        const agentName = event.agent_name || "Master Agent";
        const data = event.data || {};

        // ── L3 Intelligence Graph — build the run live from intel_* events ───────
        if (event.event_type.startsWith("intel_")) {
          const it = get().intel;
          const startedAt = it.startedAt ?? Date.now();
          const trace = [
            ...it.trace,
            { t: Date.now() - startedAt, type: event.event_type, label: intelLabel(event.event_type, data) },
          ];
          let result = it.result;
          let running = it.running;
          let investigators = it.investigators;
          let fitGraph = it.fitGraph;
          let signedOnId = it.signedOnId;
          let agentNarration = it.agentNarration;
          let narrating = it.narrating;
          switch (event.event_type) {
            case "intel_supervisor_started":
              running = true;
              investigators = initialInvestigators();
              break;
            case "intel_investigator_started":
              investigators = patchInvestigator(investigators, data.investigator as string, { status: "running" });
              break;
            case "intel_investigator_completed":
              investigators = patchInvestigator(investigators, data.investigator as string, {
                status: "done",
                eligible: data.eligible as number,
                assessed: data.assessed as number,
              });
              break;
            case "intel_ranking":
              result = {
                ...(result ?? emptyIntelResult()),
                status: "matched",
                candidates: (data.candidates as IntelRankedCandidate[]) || [],
              };
              break;
            case "intel_graph":
              fitGraph = {
                nodes: (data.nodes as IntelFitGraph["nodes"]) || [],
                edges: (data.edges as IntelFitGraph["edges"]) || [],
                backend: (data.backend as string) || "fallback",
                node_count: (data.node_count as number) || 0,
                edge_count: (data.edge_count as number) || 0,
              };
              break;
            case "intel_no_crew":
              result = {
                ...(result ?? emptyIntelResult()),
                status: "no_crew_found",
                candidates: [],
                message: (data.message as string) || "No eligible crew found",
              };
              break;
            case "intel_notification_sent":
              result = {
                ...(result ?? emptyIntelResult()),
                notifications: [
                  ...((result ?? emptyIntelResult()).notifications),
                  data as unknown as IntelNotification,
                ],
              };
              break;
            case "intel_supervisor_completed":
              running = false;
              result = {
                ...(result ?? emptyIntelResult()),
                status: (data.status as IntelResult["status"]) || "matched",
                timing: (data.timing as IntelResult["timing"]) || { first_event_ms: 0, total_ms: 0 },
              };
              break;
            case "intel_signed_on":
              // The agent signed on the rank-1 candidate (also pushed via the HTTP
              // sign-on call; handling the event keeps the badge correct if the WS
              // broadcast lands first).
              signedOnId = (data.crew_id as string) || signedOnId;
              break;
            case "intel_narration_started":
              narrating = true;
              agentNarration = [];
              break;
            case "intel_agent_message":
              agentNarration = [
                ...agentNarration,
                {
                  agent: (data.agent as string) || "Intelligence Supervisor",
                  text: (data.text as string) || "",
                  t: Date.now() - startedAt,
                },
              ];
              break;
            case "intel_narration_done":
              narrating = false;
              break;
          }
          set({ intel: { ...it, trace, result, running, startedAt, investigators, fitGraph, signedOnId, agentNarration, narrating } });
          return; // intel_* events are fully handled here
        }

        // Skill usage isn't a named event type — it's any event the backend tagged
        // category:"skill" (a read/bash that opened a SKILL.md). Count distinct skills
        // per agent, deduped by resolved name or the raw access input.
        if (data.category === "skill") {
          const key =
            (data.skill as string) || JSON.stringify(data.input ?? data.name ?? "");
          const prev = get().agentStates[agentName]?.skills_loaded || [];
          if (key && !prev.includes(key)) {
            const loaded = [...prev, key];
            updateAgentState(agentName, {
              skills_loaded: loaded,
              skills_used: loaded.length,
              last_skill: (data.skill as string) || get().agentStates[agentName]?.last_skill,
            });
          }
        }

        switch (event.event_type) {
          case "workflow_created":
            // New session → wipe the console so the trace shows only this run
            // (this also restarts per-agent iteration numbering at 1). Keep the
            // triggering event itself as the first line.
            set({ events: [event], signOnOutcome: null });
            updateActiveWorkflow({
              workflow_id: data.workflow_id as string,
              status: "running",
            });
            get().resetAgentStates();
            updateAgentState("Master Agent", { status: "running" });
            break;

          case "auto_compliance":
            set({
              signOnOutcome: {
                crewId: data.candidate_id as string,
                crewName: data.candidate_name as string,
                crewRank: data.candidate_rank as string,
                matchConfidence: data.match_confidence as number,
                phase: "validating",
              },
            });
            break;

          case "crew_signed_on":
            set({
              signOnOutcome: {
                crewId: data.crew_id as string,
                crewName: data.crew_name as string,
                crewRank: data.crew_rank as string,
                matchConfidence: data.match_confidence as number,
                phase: "signed_on",
                complianceStatus: data.compliance_status as string,
                complianceScore: data.compliance_score as number,
                reasons: (data.warnings as string[]) || [],
                recommendation: data.recommendation as string,
                subgraph: (data.subgraph as ComplianceSubgraph) || undefined,
              },
            });
            break;

          case "sign_on_rejected":
            set({
              signOnOutcome: {
                crewId: data.crew_id as string,
                crewName: data.crew_name as string,
                crewRank: data.crew_rank as string,
                matchConfidence: data.match_confidence as number,
                phase: "rejected",
                complianceStatus: data.compliance_status as string,
                complianceScore: data.compliance_score as number,
                reasons: (data.failures as string[]) || [],
                recommendation: data.recommendation as string,
                subgraph: (data.subgraph as ComplianceSubgraph) || undefined,
              },
            });
            break;

          case "sign_on_attempt_rejected":
            // A candidate failed compliance mid-retry. Surface the rejection in the
            // crew panel before the orchestrator moves on; the next candidate's
            // auto_compliance event flips this back to "validating".
            set({
              signOnOutcome: {
                crewId: data.crew_id as string,
                crewName: data.crew_name as string,
                crewRank: data.crew_rank as string,
                phase: "rejected",
                complianceStatus: data.compliance_status as string,
                complianceScore: data.compliance_score as number,
                reasons: (data.failures as string[]) || [],
              },
            });
            break;

          case "agent_started":
            updateAgentState(agentName, {
              status: "running",
              current_task: data.task as string,
            });
            break;

          case "agent_thinking":
            updateAgentState(agentName, {
              tokens_used: (get().agentStates[agentName]?.tokens_used || 0) + (data.tokens as number || 0),
            });
            break;

          case "tool_called":
            updateAgentState(agentName, {
              tool_calls: (get().agentStates[agentName]?.tool_calls || 0) + 1,
              last_tool: data.tool as string,
            });
            break;

          case "agent_completed":
            updateAgentState(agentName, {
              status: "completed",
              confidence_score: (data.result as Record<string, number>)?.confidence_score,
            });
            break;

          case "agent_failed":
            updateAgentState(agentName, { status: "failed" });
            break;

          case "master_routing":
            updateAgentState("Master Agent", {
              status: "running",
              current_task: data.action as string,
            });
            break;

          case "master_waiting":
            updateAgentState("Master Agent", { status: "waiting" });
            if (data.matched_crew) {
              const matched = data.matched_crew as Record<string, string>;
              setMatchedCandidate(matched.crew_id);
              updateActiveWorkflow({
                matched_crew: matched as unknown as WorkflowState["matched_crew"],
              });
            }
            break;

          case "sign_on_initiated":
            updateAgentState("Master Agent", { status: "running" });
            updateAgentState("Compliance Agent", { status: "pending" });
            break;

          case "workflow_completed":
            updateAgentState("Master Agent", { status: "completed" });
            updateActiveWorkflow({
              status: "completed",
              total_tokens: data.total_tokens as number,
              total_cost: data.total_cost as number,
            });
            break;

          case "workflow_failed":
            updateActiveWorkflow({ status: "failed" });
            break;

          case "workflow_paused":
            updateActiveWorkflow({ status: "paused" });
            break;

          case "workflow_resumed":
            updateActiveWorkflow({ status: "running" });
            break;

          case "workflow_cancelled":
            updateActiveWorkflow({ status: "cancelled" });
            break;

          case "timeline_update":
            if (data.entry) {
              updateActiveWorkflow({
                timeline: [
                  ...(get().activeWorkflow?.timeline || []),
                  data.entry as WorkflowState["timeline"][0],
                ],
              });
            }
            break;
        }
      },

      activeTab: "sign-off",
      setActiveTab: (tab) => set({ activeTab: tab }),
      showWorkflowPanel: false,
      setShowWorkflowPanel: (v) => set({ showWorkflowPanel: v }),
    }),
    { name: "WorkflowStore" }
  )
);
