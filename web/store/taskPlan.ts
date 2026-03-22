import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type { TaskPlan } from "@/lib/types";

const EMPTY_PLAN: TaskPlan = {
  active: false,
  task: { title: "", summary: "" },
  steps: [],
};

interface TaskPlanState {
  plan: TaskPlan;
  isLoading: boolean;

  fetchTaskPlan: (sessionId: string) => Promise<void>;
  dismissTaskPlan: (sessionId: string) => Promise<void>;
  clearTaskPlan: () => void;
}

export const useTaskPlanStore = create<TaskPlanState>((set) => ({
  plan: EMPTY_PLAN,
  isLoading: false,

  fetchTaskPlan: async (sessionId: string) => {
    set({ isLoading: true });
    try {
      const plan = await apiFetch<TaskPlan>(
        `/sessions/${sessionId}/task-plan`,
      );
      set({ plan, isLoading: false });
    } catch {
      set({ plan: EMPTY_PLAN, isLoading: false });
    }
  },

  dismissTaskPlan: async (sessionId: string) => {
    try {
      await apiFetch(`/sessions/${sessionId}/task-plan`, {
        method: "DELETE",
      });
    } catch {
      // best-effort
    }
    set({ plan: EMPTY_PLAN });
  },

  clearTaskPlan: () => {
    set({ plan: EMPTY_PLAN, isLoading: false });
  },
}));
