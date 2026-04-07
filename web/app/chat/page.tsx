"use client";

import { useEffect, useCallback, useRef, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useSessionStore } from "@/store/session";
import { useChatStore } from "@/store/chat";
import { stripThinkTags } from "@/lib/constants";
import { useWorkspaceStore } from "@/store/workspace";
import { useTaskPlanStore } from "@/store/taskPlan";
import { useSSE } from "@/hooks/useSSE";
import MessageList from "@/components/chat/MessageList";
import PromptInput from "@/components/chat/PromptInput";
import PermissionDialog from "@/components/permission/PermissionDialog";
import WaitingRecoveryBanner from "@/components/permission/WaitingRecoveryBanner";
import SessionStatusIndicator from "@/components/chat/SessionStatusIndicator";
import TaskPlanPanel from "@/components/task-plan/TaskPlanPanel";
import PolicySwitcher from "@/components/policy/PolicySwitcher";
import ContextRingGauge from "@/components/chat/ContextRingGauge";
import CompactBanner from "@/components/chat/CompactBanner";
import PanelShell from "@/components/panel/PanelShell";
import { usePanelStore } from "@/store/panel";

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionIdFromUrl = searchParams.get("s");

  const {
    sessions,
    currentSessionId,
    isLoading,
    isCreating,
    fetchSessions,
    selectSession,
    createAndSelectSession,
  } = useSessionStore();

  const {
    fetchMessages,
    fetchRuntime,
    fetchPolicy,
    fetchPendingPermissions,
    reset: resetChat,
    setStatus,
  } = useChatStore();
  const pendingPermissions = useChatStore((s) => s.pendingPermissions);
  const chatStatus = useChatStore((s) => s.status);
  const policy = useChatStore((s) => s.policy);
  const runtime = useChatStore((s) => s.runtime);
  const contextWarning = useChatStore((s) => s.contextWarning);
  const justCompacted = useChatStore((s) => s.justCompacted);

  const fetchTree = useWorkspaceStore((s) => s.fetchTree);

  const clearPanel = usePanelStore((s) => s.clearPanel);
  const fetchTasks = usePanelStore((s) => s.fetchTasks);

  const fetchTaskPlan = useTaskPlanStore((s) => s.fetchTaskPlan);
  const clearTaskPlan = useTaskPlanStore((s) => s.clearTaskPlan);

  // SSE connection
  useSSE(currentSessionId);

  // Load sessions once on mount
  useEffect(() => {
    fetchSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tracks the last URL value we processed, to avoid re-triggering on the same value.
  const lastUrlSyncRef = useRef<string | null>(null);

  // URL → store sync: whenever ?s= changes (including from child session links),
  // update the store. Only fires when the URL value actually changes, not when
  // currentSessionId changes (which would cause a loop).
  useEffect(() => {
    if (
      sessionIdFromUrl &&
      sessionIdFromUrl !== lastUrlSyncRef.current &&
      sessionIdFromUrl !== currentSessionId
    ) {
      lastUrlSyncRef.current = sessionIdFromUrl;
      selectSession(sessionIdFromUrl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionIdFromUrl]);

  // Store → URL sync: keep URL in sync when session changes from sidebar/auto-select.
  // Updates the ref so the URL→store effect doesn't re-fire for our own write.
  useEffect(() => {
    if (currentSessionId && currentSessionId !== sessionIdFromUrl) {
      lastUrlSyncRef.current = currentSessionId;
      router.replace(`/chat?s=${currentSessionId}`, { scroll: false });
    }
  }, [currentSessionId, sessionIdFromUrl, router]);

  // Auto-select or auto-create session
  const autoSelect = useCallback(async () => {
    if (isLoading || isCreating) return;

    if (sessions.length === 0) {
      await createAndSelectSession();
      return;
    }

    if (!currentSessionId) {
      const target = sessionIdFromUrl || sessions[0]?.id;
      if (target) selectSession(target);
    }
  }, [
    sessions,
    currentSessionId,
    isLoading,
    isCreating,
    sessionIdFromUrl,
    selectSession,
    createAndSelectSession,
  ]);

  useEffect(() => {
    autoSelect();
  }, [autoSelect]);

  // Session enter complete flag — gates WaitingRecoveryBanner so it doesn't
  // flash while fetchPendingPermissions is still in-flight during enter.
  const [sessionReady, setSessionReady] = useState(false);

  // Full session enter sequence when session changes:
  // 1. messages  2. runtime  3. conditional pending perms  4. policy  5. tree
  // Uses a cancellation flag so stale async chains (from rapid session switches)
  // don't overwrite state that belongs to the newer session.
  useEffect(() => {
    if (!currentSessionId) return;

    let cancelled = false;

    setSessionReady(false);
    resetChat();
    clearPanel();
    clearTaskPlan();

    const enterSession = async () => {
      // 1. Load messages (truth)
      await fetchMessages(currentSessionId);
      if (cancelled) return;

      // 2. Load runtime (truth for status recovery)
      const runtime = await fetchRuntime(currentSessionId);
      if (cancelled) return;

      // 3. If runtime says waiting, recover pending permissions
      if (runtime && runtime.status === "waiting") {
        await fetchPendingPermissions(currentSessionId);
        if (cancelled) return;
      }

      // 4. Load policy
      await fetchPolicy(currentSessionId);
      if (cancelled) return;

      // 5. Load workspace tree + task plan (non-blocking)
      fetchTree(currentSessionId);
      fetchTaskPlan(currentSessionId);
      fetchTasks(currentSessionId);

      // Mark enter complete — now WaitingRecoveryBanner can safely render
      setSessionReady(true);
    };

    enterSession();

    return () => {
      cancelled = true;
    };
  }, [
    currentSessionId,
    resetChat,
    clearPanel,
    fetchMessages,
    fetchRuntime,
    fetchPendingPermissions,
    fetchPolicy,
    fetchTree,
    fetchTaskPlan,
    clearTaskPlan,
    fetchTasks,
  ]);

  // Status badge color + user-friendly labels
  const statusColor: Record<string, string> = {
    idle: "bg-bg-tertiary text-text-secondary",
    queued: "bg-yellow-500/20 text-yellow-400",
    running: "bg-accent/20 text-accent",
    waiting: "bg-yellow-500/20 text-yellow-500",
    subtask_waiting: "bg-purple-500/20 text-purple-400",
    error: "bg-danger/20 text-danger",
  };
  const statusLabel: Record<string, string> = {
    idle: "Idle",
    queued: "Queued",
    running: "Running",
    waiting: "Waiting",
    subtask_waiting: "Sub-task",
    error: "Error",
  };

  const currentSession = sessions.find((s) => s.id === currentSessionId);

  // Only show spinner before we have a session. Once currentSessionId is set,
  // background session-list refreshes must NOT unmount the chat area.
  if (!currentSessionId) {
    return (
      <div className="flex flex-1 items-center justify-center text-text-secondary">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-1">
      {/* Chat column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Chat header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-medium">
              {stripThinkTags(currentSession?.title || "Session")}
            </h2>
            <span
              className={`rounded px-1.5 py-0.5 text-xs ${statusColor[chatStatus] || statusColor.idle}`}
            >
              {statusLabel[chatStatus] || chatStatus}
            </span>
            <SessionStatusIndicator />
            {/* Context occupancy ring gauge — visible after at least one completed turn */}
            {runtime &&
              runtime.last_call_prompt_tokens != null &&
              runtime.last_call_prompt_tokens > 0 &&
              runtime.context_window_limit != null &&
              runtime.context_usage_ratio != null && (
                <ContextRingGauge
                  ratio={runtime.context_usage_ratio}
                  promptTokens={runtime.last_call_prompt_tokens}
                  windowLimit={runtime.context_window_limit}
                />
              )}
          </div>

          {/* Policy mode indicator + switcher */}
          <PolicySwitcher sessionId={currentSessionId} />
        </div>

        {/* Task plan panel */}
        <TaskPlanPanel />

        {/* Message list */}
        <MessageList />

        {/* Permission dialog */}
        {pendingPermissions.length > 0 && (
          <PermissionDialog
            permissions={pendingPermissions}
            sessionId={currentSessionId}
          />
        )}

        {/* Waiting recovery: session is "waiting" but SSE permission_ask was missed.
            Only show after session enter completes (sessionReady) to avoid flashing
            the banner while fetchPendingPermissions is still in-flight. */}
        {sessionReady &&
          chatStatus === "waiting" &&
          pendingPermissions.length === 0 && (
            <WaitingRecoveryBanner sessionId={currentSessionId} />
          )}

        {/* Subtask waiting banner */}
        {chatStatus === "subtask_waiting" && (
          <div className="flex items-center gap-2 border-t border-purple-500/20 bg-purple-500/5 px-4 py-2">
            <div className="h-2 w-2 animate-pulse rounded-full bg-purple-400" />
            <span className="text-xs text-purple-400">
              Agent is waiting for a child task to complete
            </span>
            <button
              onClick={() => {
                const { openTaskOutput } = usePanelStore.getState();
                openTaskOutput();
              }}
              className="ml-auto rounded bg-purple-500/20 px-2 py-0.5 text-[10px] text-purple-400 transition hover:bg-purple-500/30"
            >
              View Task
            </button>
          </div>
        )}

        {/* Compact banner: show when SSE context_warning or ratio > 0.7,
            but suppress after a successful compact until next real model call */}
        {!justCompacted &&
          (contextWarning ||
            (runtime?.context_usage_ratio != null &&
              runtime.context_usage_ratio > 0.7)) &&
          chatStatus === "idle" && (
            <CompactBanner sessionId={currentSessionId} />
          )}

        {/* Input */}
        <PromptInput sessionId={currentSessionId} />
      </div>

      {/* Work panel (half-screen overlay) */}
      <PanelShell sessionId={currentSessionId} />
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      }
    >
      <ChatPageInner />
    </Suspense>
  );
}
