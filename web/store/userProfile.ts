import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type {
  KnowledgeDocItem,
  Message,
  Session,
  SquareDetailResponse,
  UserProfile,
} from "@/lib/types";

interface UserProfileState {
  profile: UserProfile | null;
  isLoading: boolean;
  error: string | null;
  knowledgeDocs: KnowledgeDocItem[];
  knowledgeLoading: boolean;
  sessions: Session[];
  sessionsLoading: boolean;
  viewingSessionId: string | null;
  viewingMessages: Message[];
  viewingMessagesLoading: boolean;

  // Skill detail drawer
  selectedSkill: string | null;
  skillDetail: SquareDetailResponse | null;
  skillDetailLoading: boolean;

  fetchProfile: () => Promise<void>;
  fetchKnowledgeDocs: () => Promise<void>;
  fetchSessions: () => Promise<void>;
  viewSessionMessages: (sessionId: string) => Promise<void>;
  clearViewingSession: () => void;
  selectSkill: (skillName: string) => void;
  clearSkillDetail: () => void;
}

export const useUserProfileStore = create<UserProfileState>((set, get) => ({
  profile: null,
  isLoading: false,
  error: null,
  knowledgeDocs: [],
  knowledgeLoading: false,
  sessions: [],
  sessionsLoading: false,
  viewingSessionId: null,
  viewingMessages: [],
  viewingMessagesLoading: false,
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

  fetchKnowledgeDocs: async () => {
    set({ knowledgeLoading: true });
    try {
      const docs = await apiFetch<KnowledgeDocItem[]>("/knowledge/documents");
      const profile = get().profile;
      set({
        knowledgeDocs: profile
          ? docs.filter((doc) => doc.owner === profile.id)
          : docs,
        knowledgeLoading: false,
      });
    } catch {
      set({ knowledgeDocs: [], knowledgeLoading: false });
    }
  },

  fetchSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const sessions = await apiFetch<Session[]>("/sessions");
      set({ sessions, sessionsLoading: false });
    } catch {
      set({ sessions: [], sessionsLoading: false });
    }
  },

  viewSessionMessages: async (sessionId: string) => {
    set({
      viewingSessionId: sessionId,
      viewingMessages: [],
      viewingMessagesLoading: true,
    });
    try {
      const messages = await apiFetch<Message[]>(
        `/sessions/${encodeURIComponent(sessionId)}/messages`,
      );
      if (get().viewingSessionId === sessionId) {
        set({ viewingMessages: messages, viewingMessagesLoading: false });
      }
    } catch {
      if (get().viewingSessionId === sessionId) {
        set({ viewingMessages: [], viewingMessagesLoading: false });
      }
    }
  },

  clearViewingSession: () => {
    set({
      viewingSessionId: null,
      viewingMessages: [],
      viewingMessagesLoading: false,
    });
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
