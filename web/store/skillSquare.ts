import { create } from "zustand";
import { apiFetch, ApiRequestError } from "@/lib/api";
import type { SquareCardItem, SquareDetailResponse } from "@/lib/types";

interface SkillSquareState {
  // List
  cards: SquareCardItem[];
  cardsLoading: boolean;
  searchQuery: string;

  // Detail drawer
  selectedSkill: string | null;
  detail: SquareDetailResponse | null;
  detailLoading: boolean;

  // Install/uninstall
  actionLoading: boolean;
  actionError: string | null;

  // Actions
  fetchCards: (q?: string) => Promise<void>;
  setSearchQuery: (q: string) => void;
  selectSkill: (name: string) => void;
  selectSkillVersion: (name: string, version: string) => void;
  clearDetail: () => void;
  clearActionError: () => void;
  installSkill: (skillId: string) => Promise<void>;
  uninstallSkill: (skillId: string) => Promise<void>;
}

export const useSkillSquareStore = create<SkillSquareState>((set, get) => ({
  cards: [],
  cardsLoading: false,
  searchQuery: "",

  selectedSkill: null,
  detail: null,
  detailLoading: false,

  actionLoading: false,
  actionError: null,

  fetchCards: async (q?: string) => {
    set({ cardsLoading: true });
    try {
      const query = q !== undefined ? q : get().searchQuery;
      const path = query
        ? `/skills/square?q=${encodeURIComponent(query)}`
        : "/skills/square";
      const cards = await apiFetch<SquareCardItem[]>(path);
      // If selected skill is no longer in the filtered results, clear detail
      const { selectedSkill } = get();
      const stillVisible = !selectedSkill || cards.some((c) => c.name === selectedSkill);
      if (!stillVisible) {
        set({ cards, cardsLoading: false, selectedSkill: null, detail: null, detailLoading: false });
      } else {
        set({ cards, cardsLoading: false });
      }
    } catch {
      set({ cards: [], cardsLoading: false });
    }
  },

  setSearchQuery: (q: string) => {
    set({ searchQuery: q });
  },

  selectSkill: async (name: string) => {
    set({ selectedSkill: name, detail: null, detailLoading: true, actionError: null });
    try {
      const detail = await apiFetch<SquareDetailResponse>(
        `/skills/square/${encodeURIComponent(name)}`,
      );
      if (get().selectedSkill === name) {
        set({ detail, detailLoading: false });
      }
    } catch {
      if (get().selectedSkill === name) {
        set({ detail: null, detailLoading: false });
      }
    }
  },

  selectSkillVersion: async (name: string, version: string) => {
    set({ selectedSkill: name, detail: null, detailLoading: true, actionError: null });
    try {
      const detail = await apiFetch<SquareDetailResponse>(
        `/skills/square/${encodeURIComponent(name)}?version=${encodeURIComponent(version)}`,
      );
      if (get().selectedSkill === name) {
        set({ detail, detailLoading: false });
      }
    } catch {
      if (get().selectedSkill === name) {
        set({ detail: null, detailLoading: false });
      }
    }
  },

  clearDetail: () => {
    set({ selectedSkill: null, detail: null, detailLoading: false, actionError: null });
  },

  clearActionError: () => {
    set({ actionError: null });
  },

  installSkill: async (skillId: string) => {
    set({ actionLoading: true, actionError: null });
    try {
      await apiFetch(`/skills/${skillId}/install`, { method: "POST" });
      // Refresh both list and detail
      const { selectedSkill, fetchCards } = get();
      await fetchCards();
      if (selectedSkill) {
        const detail = await apiFetch<SquareDetailResponse>(
          `/skills/square/${encodeURIComponent(selectedSkill)}`,
        );
        set({ detail, actionLoading: false });
      } else {
        set({ actionLoading: false });
      }
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : "Install failed";
      set({ actionLoading: false, actionError: message });
    }
  },

  uninstallSkill: async (skillId: string) => {
    set({ actionLoading: true, actionError: null });
    try {
      await apiFetch(`/skills/${skillId}/uninstall`, { method: "DELETE" });
      // Refresh both list and detail
      const { selectedSkill, fetchCards } = get();
      await fetchCards();
      if (selectedSkill) {
        const detail = await apiFetch<SquareDetailResponse>(
          `/skills/square/${encodeURIComponent(selectedSkill)}`,
        );
        set({ detail, actionLoading: false });
      } else {
        set({ actionLoading: false });
      }
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.message
          : "Uninstall failed";
      set({ actionLoading: false, actionError: message });
    }
  },
}));
