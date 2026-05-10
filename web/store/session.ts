import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import { stripThinkTags } from "@/lib/constants";
import type { LoadedSkill, Session, SessionStatus } from "@/lib/types";

interface SessionState {
  sessions: Session[];
  currentSessionId: string | null;
  isLoading: boolean;
  isCreating: boolean;

  fetchSessions: () => Promise<void>;
  createSession: (title?: string) => Promise<Session>;
  createAndSelectSession: (title?: string) => Promise<Session>;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => Promise<void>;
  updateSessionStatus: (id: string, status: SessionStatus) => void;
  updateSessionTitle: (id: string, title: string) => void;
  updateSessionLoadedSkills: (id: string, loadedSkills: LoadedSkill[]) => void;
  saveSessionTitle: (id: string, title: string) => Promise<Session>;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  // Start as true so autoSelect() won't fire before initial fetchSessions() resolves
  isLoading: true,
  isCreating: false,

  fetchSessions: async () => {
    set({ isLoading: true });
    try {
      const raw = await apiFetch<Session[]>("/sessions");
      // Clean titles at ingestion boundary
      const fetched = raw.map((s) => ({
        ...s,
        title: stripThinkTags(s.title),
      }));
      set((prev) => {
        // Preserve the currently selected session if the server response
        // doesn't include it yet (race with createAndSelectSession).
        let sessions = fetched;
        if (prev.currentSessionId) {
          const inFetched = fetched.some((s) => s.id === prev.currentSessionId);
          if (!inFetched) {
            const current = prev.sessions.find((s) => s.id === prev.currentSessionId);
            if (current) {
              sessions = [current, ...fetched];
            }
          }
        }
        return { sessions, isLoading: false };
      });
    } catch {
      set({ isLoading: false });
    }
  },

  createSession: async (title?: string) => {
    const session = await apiFetch<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify({
        title: title || "New Session",
        agent_id: "assistant",
      }),
    });
    set((s) => ({ sessions: [session, ...s.sessions] }));
    return session;
  },

  // Atomic: create + select in a single set() to avoid split-render race
  createAndSelectSession: async (title?: string) => {
    // Prevent concurrent creation (autoSelect + user click race)
    if (get().isCreating) return get().sessions[0] ?? ({ id: "", title: "New Session" } as Session);
    set({ isCreating: true });
    try {
      const session = await apiFetch<Session>("/sessions", {
        method: "POST",
        body: JSON.stringify({
          title: title || "New Session",
          agent_id: "assistant",
        }),
      });
      set((s) => ({
        sessions: [session, ...s.sessions],
        currentSessionId: session.id,
        isCreating: false,
      }));
      return session;
    } catch (err) {
      set({ isCreating: false });
      throw err;
    }
  },

  selectSession: (id: string) => {
    set({ currentSessionId: id });
  },

  deleteSession: async (id: string) => {
    try {
      await apiFetch(`/sessions/${id}`, { method: "DELETE" });
    } catch {
      // Network or server failure — don't remove from UI
      throw new Error("Failed to delete session. Please try again.");
    }
    set((s) => {
      const sessions = s.sessions.filter((x) => x.id !== id);
      const currentSessionId =
        s.currentSessionId === id
          ? sessions[0]?.id || null
          : s.currentSessionId;
      return { sessions, currentSessionId };
    });
  },

  updateSessionStatus: (id: string, status: SessionStatus) => {
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === id ? { ...x, status } : x,
      ),
    }));
  },

  updateSessionTitle: (id: string, title: string) => {
    const cleaned = stripThinkTags(title);
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === id ? { ...x, title: cleaned } : x,
      ),
    }));
  },

  updateSessionLoadedSkills: (id: string, loadedSkills: LoadedSkill[]) => {
    set((s) => ({
      sessions: s.sessions.map((x) =>
        x.id === id ? { ...x, loaded_skills: loadedSkills } : x,
      ),
    }));
  },

  saveSessionTitle: async (id: string, title: string) => {
    const cleaned = stripThinkTags(title).replace(/\s+/g, " ").trim();
    if (!cleaned) {
      throw new Error("标题不能为空");
    }
    if (cleaned.length > 80) {
      throw new Error("标题不能超过 80 个字符");
    }
    const session = await apiFetch<Session>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title: cleaned }),
    });
    const normalized = { ...session, title: stripThinkTags(session.title) };
    set((s) => ({
      sessions: s.sessions.map((x) => (x.id === id ? normalized : x)),
    }));
    return normalized;
  },
}));
