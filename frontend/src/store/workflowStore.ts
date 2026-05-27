import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type {
  WorkflowState,
  CrewMember,
  WSEvent,
  AgentExecution,
  AgentStatus,
} from "@/types";

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

  // Sign-on outcome for the active workflow: which crew was matched + the
  // compliance verdict (signed on / rejected + reasons).
  signOnOutcome: SignOnOutcome | null;

  // WebSocket event handler
  handleWSEvent: (event: WSEvent) => void;

  // UI state
  activeTab: "sign-on" | "sign-off";
  setActiveTab: (tab: "sign-on" | "sign-off") => void;
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

      events: [],
      // Keep a full session's worth of events (a multiagent run emits many);
      // the buffer is wiped on the next sign-off (workflow_created), so it stays
      // bounded without dropping logs mid-session.
      addEvent: (e) =>
        set((s) => ({ events: [e, ...s.events].slice(0, 2000) })),
      clearEvents: () => set({ events: [] }),

      handleWSEvent: (event) => {
        const { updateAgentState, updateActiveWorkflow, setMatchedCandidate, addEvent } = get();
        addEvent(event);

        const agentName = event.agent_name || "Master Agent";
        const data = event.data || {};

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
