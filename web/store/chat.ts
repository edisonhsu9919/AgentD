import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type {
  Message,
  SessionStatus,
  StreamingToolCall,
  SSEPermissionAsk,
  Runtime,
  SessionPolicy,
  PolicyMode,
  PermissionRecord,
} from "@/lib/types";

interface ChatState {
  messages: Message[];
  streamingDraft: string;
  streamingThinking: string;
  streamingToolCalls: StreamingToolCall[];
  pendingPermissions: SSEPermissionAsk[];
  status: SessionStatus;
  runtime: Runtime | null;
  policy: SessionPolicy | null;
  isLoading: boolean;
  contextWarning: boolean;
  justCompacted: boolean;

  fetchMessages: (sessionId: string) => Promise<void>;
  sendPrompt: (sessionId: string, content: string) => Promise<void>;
  cancelTask: (sessionId: string) => Promise<void>;

  // Runtime & Policy
  fetchRuntime: (sessionId: string) => Promise<Runtime | null>;
  fetchPolicy: (sessionId: string) => Promise<void>;
  updatePolicy: (sessionId: string, mode: PolicyMode, resetRules?: boolean) => Promise<void>;

  // Permission recovery
  fetchPendingPermissions: (sessionId: string) => Promise<void>;

  // Compaction
  compactContext: (sessionId: string) => Promise<void>;
  setContextWarning: (warning: boolean) => void;
  clearJustCompacted: () => void;

  // Skill picker → prompt input bridge
  pendingInsert: string | null;
  insertToPrompt: (text: string) => void;
  clearPendingInsert: () => void;

  // SSE handlers
  appendStreamingDraft: (text: string) => void;
  appendStreamingThinking: (text: string) => void;
  addStreamingToolCall: (tc: StreamingToolCall) => void;
  updateStreamingToolResult: (
    toolCallId: string,
    output: string,
    isError: boolean,
  ) => void;
  clearStreaming: () => void;
  setStatus: (status: SessionStatus) => void;

  // Permission
  addPendingPermission: (p: SSEPermissionAsk) => void;
  removePendingPermission: (permissionId: string) => void;
  clearPendingPermissions: () => void;

  // Reset on session switch
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  streamingDraft: "",
  streamingThinking: "",
  streamingToolCalls: [],
  pendingPermissions: [],
  status: "idle",
  runtime: null,
  policy: null,
  isLoading: false,
  contextWarning: false,
  justCompacted: false,
  pendingInsert: null,

  insertToPrompt: (text: string) => {
    set({ pendingInsert: text });
  },

  clearPendingInsert: () => {
    set({ pendingInsert: null });
  },

  fetchMessages: async (sessionId: string) => {
    set({ isLoading: true });
    try {
      const messages = await apiFetch<Message[]>(
        `/sessions/${sessionId}/messages`,
      );
      set({ messages, isLoading: false });
    } catch {
      set({ isLoading: false });
    }
  },

  sendPrompt: async (sessionId: string, content: string) => {
    // Optimistic: insert user message + set status to queued immediately
    const optimisticMsg: Message = {
      id: `optimistic-${Date.now()}`,
      session_id: sessionId,
      role: "user",
      parts: [{ type: "text", content }],
      is_summary: false,
      token_usage: null,
      seq: 0,
      created_at: new Date().toISOString(),
    };
    set((s) => ({
      messages: [...s.messages, optimisticMsg],
      status: "queued",
    }));

    try {
      await apiFetch(`/sessions/${sessionId}/prompt`, {
        method: "POST",
        body: JSON.stringify({ content, attachments: null }),
      });
    } catch (err) {
      // Revert optimistic changes on POST failure
      set((s) => ({
        messages: s.messages.filter((m) => m.id !== optimisticMsg.id),
        status: "idle",
      }));
      throw err;
    }

    // Refetch from backend truth to replace optimistic message with real one
    try {
      const messages = await apiFetch<Message[]>(
        `/sessions/${sessionId}/messages`,
      );
      set({ messages });
    } catch {
      // non-fatal: SSE done will refetch anyway
    }
  },

  cancelTask: async (sessionId: string) => {
    await apiFetch(`/sessions/${sessionId}/cancel-task`, { method: "DELETE" });
    // Post-cancel: clear all in-flight local state
    set({
      streamingDraft: "",
      streamingThinking: "",
      streamingToolCalls: [],
      pendingPermissions: [],
      status: "idle",
    });
    // Re-fetch backend truth
    try {
      const [messages, runtime] = await Promise.all([
        apiFetch<Message[]>(`/sessions/${sessionId}/messages`),
        apiFetch<Runtime>(`/sessions/${sessionId}/runtime`),
      ]);
      set({ messages, runtime, status: runtime.status });
    } catch {
      // best-effort
    }
  },

  compactContext: async (sessionId: string) => {
    await apiFetch(`/sessions/${sessionId}/compact`, { method: "POST" });
    // Post-compact: refetch messages + runtime for updated state
    try {
      const [messages, runtime] = await Promise.all([
        apiFetch<Message[]>(`/sessions/${sessionId}/messages`),
        apiFetch<Runtime>(`/sessions/${sessionId}/runtime`),
      ]);
      set({ messages, runtime, status: runtime.status, contextWarning: false, justCompacted: true });
    } catch {
      // best-effort
    }
  },

  setContextWarning: (warning: boolean) => {
    set({ contextWarning: warning });
  },

  clearJustCompacted: () => {
    set({ justCompacted: false });
  },

  fetchRuntime: async (sessionId: string) => {
    try {
      const runtime = await apiFetch<Runtime>(
        `/sessions/${sessionId}/runtime`,
      );
      set({ runtime, status: runtime.status });
      return runtime;
    } catch {
      set({ runtime: null });
      return null;
    }
  },

  fetchPolicy: async (sessionId: string) => {
    try {
      const policy = await apiFetch<SessionPolicy>(
        `/sessions/${sessionId}/policy`,
      );
      set({ policy });
    } catch {
      set({ policy: null });
    }
  },

  updatePolicy: async (sessionId: string, mode: PolicyMode, resetRules = false) => {
    const policy = await apiFetch<SessionPolicy>(
      `/sessions/${sessionId}/policy`,
      {
        method: "PATCH",
        body: JSON.stringify({ mode, reset_rules: resetRules }),
      },
    );
    set({ policy });
  },

  fetchPendingPermissions: async (sessionId: string) => {
    try {
      const records = await apiFetch<PermissionRecord[]>(
        `/sessions/${sessionId}/permissions/pending`,
      );
      // Always replace — if backend says 0, clear the list
      const perms: SSEPermissionAsk[] = records.map((r) => ({
        event: "permission_ask" as const,
        session_id: r.session_id,
        permission_id: r.id,
        tool_call_id: r.tool_call_id,
        tool_name: r.tool_name,
        input: r.input,
        timestamp: r.created_at,
      }));
      set({ pendingPermissions: perms });
    } catch {
      // endpoint may not be available yet
    }
  },

  appendStreamingDraft: (text: string) => {
    set((s) => ({ streamingDraft: s.streamingDraft + text }));
  },

  appendStreamingThinking: (text: string) => {
    set((s) => ({ streamingThinking: s.streamingThinking + text }));
  },

  addStreamingToolCall: (tc: StreamingToolCall) => {
    set((s) => {
      // Dedup by tool_call_id: replace if exists, append if new
      const exists = s.streamingToolCalls.some(
        (t) => t.tool_call_id === tc.tool_call_id,
      );
      return {
        streamingToolCalls: exists
          ? s.streamingToolCalls.map((t) =>
              t.tool_call_id === tc.tool_call_id ? tc : t,
            )
          : [...s.streamingToolCalls, tc],
      };
    });
  },

  updateStreamingToolResult: (
    toolCallId: string,
    output: string,
    isError: boolean,
  ) => {
    set((s) => ({
      streamingToolCalls: s.streamingToolCalls.map((tc) =>
        tc.tool_call_id === toolCallId
          ? {
              ...tc,
              status: isError ? "error" : "completed",
              output,
              is_error: isError,
            }
          : tc,
      ),
    }));
  },

  clearStreaming: () => {
    set({ streamingDraft: "", streamingThinking: "", streamingToolCalls: [] });
  },

  setStatus: (status: SessionStatus) => {
    set({ status });
  },

  addPendingPermission: (p: SSEPermissionAsk) => {
    set((s) => {
      // Dedup by permission_id: replace if exists, append if new
      const exists = s.pendingPermissions.some(
        (x) => x.permission_id === p.permission_id,
      );
      return {
        pendingPermissions: exists
          ? s.pendingPermissions.map((x) =>
              x.permission_id === p.permission_id ? p : x,
            )
          : [...s.pendingPermissions, p],
      };
    });
  },

  removePendingPermission: (permissionId: string) => {
    set((s) => ({
      pendingPermissions: s.pendingPermissions.filter(
        (p) => p.permission_id !== permissionId,
      ),
    }));
  },

  clearPendingPermissions: () => {
    set({ pendingPermissions: [] });
  },

  reset: () => {
    set({
      messages: [],
      streamingDraft: "",
      streamingThinking: "",
      streamingToolCalls: [],
      pendingPermissions: [],
      status: "idle",
      runtime: null,
      policy: null,
      isLoading: false,
      contextWarning: false,
      justCompacted: false,
      pendingInsert: null,
    });
  },
}));
