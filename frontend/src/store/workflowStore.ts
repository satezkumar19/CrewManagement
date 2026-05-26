import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type {
  WorkflowState,
  CrewMember,
  WSEvent,
  AgentExecution,
  AgentStatus,
  RejectedCandidate,
} from "@/types";

export interface ComplianceRetryState {
  attempt: number;
  max_retries: number;
  rejected_name: string;
  reason: string;
  next_candidate_name?: string;
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

  // Compliance retry banner state (cleared when a workflow completes/fails)
  complianceRetry: ComplianceRetryState | null;
  setComplianceRetry: (v: ComplianceRetryState | null) => void;

  // Events log
  events: WSEvent[];
  addEvent: (e: WSEvent) => void;
  clearEvents: () => void;

  // WebSocket event handler
  handleWSEvent: (event: WSEvent) => void;

  // UI state
  activeTab: "sign-on" | "sign-off";
  setActiveTab: (tab: "sign-on" | "sign-off") => void;
  showWorkflowPanel: boolean;
  setShowWorkflowPanel: (v: boolean) => void;
}

const DEFAULT_AGENT_STATES: Record<string, AgentLiveState> = {
  "Master Agent": { name: "Master Agent", status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0 },
  "Crew Matching Agent": { name: "Crew Matching Agent", status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0 },
  "Travel Agent": { name: "Travel Agent", status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0 },
  "Notification Agent": { name: "Notification Agent", status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0 },
  "Compliance Agent": { name: "Compliance Agent", status: "idle", tokens_used: 0, estimated_cost: 0, tool_calls: 0 },
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

      complianceRetry: null,
      setComplianceRetry: (v) => set({ complianceRetry: v }),

      events: [],
      addEvent: (e) =>
        set((s) => ({ events: [e, ...s.events].slice(0, 200) })),
      clearEvents: () => set({ events: [] }),

      handleWSEvent: (event) => {
        const {
          updateAgentState,
          updateActiveWorkflow,
          setMatchedCandidate,
          setComplianceRetry,
          addEvent,
        } = get();
        addEvent(event);

        const agentName = event.agent_name || "Master Agent";
        const data = event.data || {};

        switch (event.event_type) {
          case "workflow_created":
            updateActiveWorkflow({
              workflow_id: data.workflow_id as string,
              status: "running",
            });
            get().resetAgentStates();
            setComplianceRetry(null);
            updateAgentState("Master Agent", { status: "running" });
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
            setComplianceRetry(null);
            break;

          case "compliance_retry": {
            const rejected = data.rejected as Record<string, unknown> | undefined;
            const next = data.next_candidate as Record<string, unknown> | undefined;
            setComplianceRetry({
              attempt: Number(data.attempt) || 1,
              max_retries: Number(data.max_retries) || 3,
              rejected_name: (rejected?.name as string) || "previous candidate",
              reason: (data.reason as string) || "Compliance failed",
              next_candidate_name: next?.name as string | undefined,
            });
            updateActiveWorkflow({ status: "retrying_compliance" });
            // Compliance Agent will re-run; reset its live state so the panel
            // doesn't look like it's still "completed" from the previous attempt.
            updateAgentState("Compliance Agent", { status: "pending" });
            if (next?.crew_id) {
              setMatchedCandidate(next.crew_id as string);
            }
            break;
          }

          case "compliance_phase_complete":
            // Master finished one compliance attempt; service decides whether
            // to retry or complete. Nothing to do here — we react to the
            // follow-up compliance_retry / workflow_completed / workflow_failed.
            break;

          case "workflow_completed":
            updateAgentState("Master Agent", { status: "completed" });
            updateActiveWorkflow({
              status: "completed",
              total_tokens: data.total_tokens as number,
              total_cost: data.total_cost as number,
              rejected_candidates: (data.rejected_candidates as RejectedCandidate[]) || [],
              compliance_retries: data.retries as number | undefined,
              matched_crew: (data.final_candidate as WorkflowState["matched_crew"]) ?? undefined,
            });
            setComplianceRetry(null);
            break;

          case "workflow_failed":
            updateActiveWorkflow({
              status: "failed",
              rejected_candidates: (data.rejected_candidates as RejectedCandidate[]) || [],
            });
            setComplianceRetry(null);
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
