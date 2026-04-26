import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import { API_URL } from "@/lib/constants";
import { getToken } from "@/lib/api";
import type {
  User,
  CreateUserRequest,
  UpdateUserRequest,
  AdminUserListItem,
  UserProfile,
  UserSkillItem,
  SkillToggleResult,
  Session,
  Message,
  SquareDetailResponse,
  KnowledgeDocItem,
} from "@/lib/types";

interface AdminState {
  // --- User list ---
  users: AdminUserListItem[];
  total: number;
  page: number;
  pageSize: number;
  isLoading: boolean;
  error: string | null;

  fetchUsers: (page?: number, pageSize?: number) => Promise<void>;
  createUser: (req: CreateUserRequest) => Promise<User>;
  updateUser: (id: string, req: UpdateUserRequest) => Promise<User>;
  clearError: () => void;

  // --- User detail ---
  userDetail: UserProfile | null;
  userDetailLoading: boolean;
  fetchUserDetail: (userId: string) => Promise<void>;
  userKnowledgeDocs: KnowledgeDocItem[];
  userKnowledgeLoading: boolean;
  fetchUserKnowledgeDocs: (userId: string) => Promise<void>;

  // --- User skills ---
  userSkills: UserSkillItem[];
  userSkillsLoading: boolean;
  fetchUserSkills: (userId: string) => Promise<void>;
  toggleUserSkill: (userId: string, skillName: string, enabled: boolean) => Promise<void>;

  // --- Skill detail drawer ---
  selectedSkill: string | null;
  skillDetail: SquareDetailResponse | null;
  skillDetailLoading: boolean;
  selectSkill: (skillName: string) => void;
  clearSkillDetail: () => void;

  // --- User sessions ---
  userSessions: Session[];
  userSessionsTotal: number;
  userSessionsPage: number;
  userSessionsLoading: boolean;
  fetchUserSessions: (userId: string, page?: number) => Promise<void>;

  // --- Session messages (read-only) ---
  viewingSessionId: string | null;
  viewingMessages: Message[];
  viewingMessagesLoading: boolean;
  viewSessionMessages: (userId: string, sessionId: string) => Promise<void>;
  clearViewingSession: () => void;
}

export const useAdminStore = create<AdminState>((set, get) => ({
  // --- User list ---
  users: [],
  total: 0,
  page: 1,
  pageSize: 20,
  isLoading: false,
  error: null,

  fetchUsers: async (page = 1, pageSize = 20) => {
    set({ isLoading: true, error: null });
    try {
      const token = getToken();
      const res = await fetch(
        `${API_URL}/admin/users?page=${page}&page_size=${pageSize}`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!res.ok) {
        const json = await res.json();
        throw new Error(json.error?.message || res.statusText);
      }
      const json = await res.json();
      set({
        users: json.data as AdminUserListItem[],
        total: json.meta?.total ?? 0,
        page: json.meta?.page ?? page,
        pageSize: json.meta?.page_size ?? pageSize,
        isLoading: false,
      });
    } catch (err) {
      set({
        isLoading: false,
        error: err instanceof Error ? err.message : "Failed to load users",
      });
    }
  },

  createUser: async (req: CreateUserRequest) => {
    set({ error: null });
    try {
      const user = await apiFetch<User>("/admin/users", {
        method: "POST",
        body: JSON.stringify(req),
      });
      const { page, pageSize } = get();
      await get().fetchUsers(page, pageSize);
      return user;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create user";
      set({ error: msg });
      throw err;
    }
  },

  updateUser: async (id: string, req: UpdateUserRequest) => {
    set({ error: null });
    try {
      const user = await apiFetch<User>(`/admin/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(req),
      });
      set((s) => ({
        users: s.users.map((u) => (u.id === id ? { ...u, ...user } : u)),
      }));
      return user;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to update user";
      set({ error: msg });
      throw err;
    }
  },

  clearError: () => set({ error: null }),

  // --- User detail ---
  userDetail: null,
  userDetailLoading: false,
  userKnowledgeDocs: [],
  userKnowledgeLoading: false,

  fetchUserDetail: async (userId: string) => {
    set({ userDetailLoading: true });
    try {
      const detail = await apiFetch<UserProfile>(`/admin/users/${userId}`);
      set({ userDetail: detail, userDetailLoading: false });
    } catch {
      set({ userDetail: null, userDetailLoading: false });
    }
  },

  fetchUserKnowledgeDocs: async (userId: string) => {
    set({ userKnowledgeLoading: true });
    try {
      const docs = await apiFetch<KnowledgeDocItem[]>("/knowledge/documents");
      set({
        userKnowledgeDocs: docs.filter((doc) => doc.owner === userId),
        userKnowledgeLoading: false,
      });
    } catch {
      set({ userKnowledgeDocs: [], userKnowledgeLoading: false });
    }
  },

  // --- User skills ---
  userSkills: [],
  userSkillsLoading: false,

  fetchUserSkills: async (userId: string) => {
    set({ userSkillsLoading: true });
    try {
      const token = getToken();
      const res = await fetch(
        `${API_URL}/admin/users/${userId}/skills`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!res.ok) throw new Error("Failed to fetch skills");
      const json = await res.json();
      set({ userSkills: json.data as UserSkillItem[], userSkillsLoading: false });
    } catch {
      set({ userSkills: [], userSkillsLoading: false });
    }
  },

  toggleUserSkill: async (userId: string, skillName: string, enabled: boolean) => {
    try {
      await apiFetch<SkillToggleResult>(
        `/admin/users/${userId}/skills/${encodeURIComponent(skillName)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ is_enabled: enabled }),
        },
      );
      // Re-fetch to get truth
      await get().fetchUserSkills(userId);
      // Also refresh user detail if loaded
      if (get().userDetail?.id === userId) {
        await get().fetchUserDetail(userId);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to toggle skill";
      set({ error: msg });
    }
  },

  // --- Skill detail drawer ---
  selectedSkill: null,
  skillDetail: null,
  skillDetailLoading: false,

  selectSkill: async (skillName: string) => {
    set({ selectedSkill: skillName, skillDetail: null, skillDetailLoading: true });
    try {
      const detail = await apiFetch<SquareDetailResponse>(
        `/skills/square/${encodeURIComponent(skillName)}`,
      );
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

  // --- User sessions ---
  userSessions: [],
  userSessionsTotal: 0,
  userSessionsPage: 1,
  userSessionsLoading: false,

  fetchUserSessions: async (userId: string, page = 1) => {
    set({ userSessionsLoading: true });
    try {
      const token = getToken();
      const res = await fetch(
        `${API_URL}/admin/users/${userId}/sessions?page=${page}&page_size=20`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!res.ok) throw new Error("Failed to fetch sessions");
      const json = await res.json();
      set({
        userSessions: json.data as Session[],
        userSessionsTotal: json.meta?.total ?? 0,
        userSessionsPage: json.meta?.page ?? page,
        userSessionsLoading: false,
      });
    } catch {
      set({ userSessions: [], userSessionsLoading: false });
    }
  },

  // --- Session messages (read-only) ---
  viewingSessionId: null,
  viewingMessages: [],
  viewingMessagesLoading: false,

  viewSessionMessages: async (userId: string, sessionId: string) => {
    set({ viewingSessionId: sessionId, viewingMessages: [], viewingMessagesLoading: true });
    try {
      const token = getToken();
      const res = await fetch(
        `${API_URL}/admin/users/${userId}/sessions/${sessionId}/messages`,
        {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!res.ok) throw new Error("Failed to fetch messages");
      const json = await res.json();
      if (get().viewingSessionId === sessionId) {
        set({ viewingMessages: json.data as Message[], viewingMessagesLoading: false });
      }
    } catch {
      if (get().viewingSessionId === sessionId) {
        set({ viewingMessages: [], viewingMessagesLoading: false });
      }
    }
  },

  clearViewingSession: () => {
    set({ viewingSessionId: null, viewingMessages: [], viewingMessagesLoading: false });
  },
}));
