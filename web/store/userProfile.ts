import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type { UserProfile, SquareDetailResponse } from "@/lib/types";

interface UserProfileState {
  profile: UserProfile | null;
  isLoading: boolean;
  error: string | null;

  // Skill detail drawer
  selectedSkill: string | null;
  skillDetail: SquareDetailResponse | null;
  skillDetailLoading: boolean;

  fetchProfile: () => Promise<void>;
  selectSkill: (skillName: string) => void;
  clearSkillDetail: () => void;
}

export const useUserProfileStore = create<UserProfileState>((set, get) => ({
  profile: null,
  isLoading: false,
  error: null,
  selectedSkill: null,
  skillDetail: null,
  skillDetailLoading: false,

  fetchProfile: async () => {
    set({ isLoading: true, error: null });
    try {
      const profile = await apiFetch<UserProfile>("/auth/me/profile");
      set({ profile, isLoading: false });
    } catch (err) {
      set({
        isLoading: false,
        error: err instanceof Error ? err.message : "Failed to load profile",
      });
    }
  },

  selectSkill: async (skillName: string) => {
    set({ selectedSkill: skillName, skillDetail: null, skillDetailLoading: true });
    try {
      const detail = await apiFetch<SquareDetailResponse>(
        `/skills/square/${encodeURIComponent(skillName)}`,
      );
      // Only update if still viewing same skill
      if (get().selectedSkill === skillName) {
        set({ skillDetail: detail, skillDetailLoading: false });
      }
    } catch {
      if (get().selectedSkill === skillName) {
        set({ skillDetail: null, skillDetailLoading: false });
      }
    }
  },

  clearSkillDetail: () => {
    set({ selectedSkill: null, skillDetail: null, skillDetailLoading: false });
  },
}));
