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
import FilePreview from "@/components/preview/FilePreview";
import PolicySwitcher from "@/components/policy/PolicySwitcher";

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

  const fetchTree = useWorkspaceStore((s) => s.fetchTree);
  const clearSelection = useWorkspaceStore((s) => s.clearSelection);
  const selectedFile = useWorkspaceStore((s) => s.selectedFile);

  const fetchTaskPlan = useTaskPlanStore((s) => s.fetchTaskPlan);
  const clearTaskPlan = useTaskPlanStore((s) => s.clearTaskPlan);

  // SSE connection
  useSSE(currentSessionId);

  // Load sessions once on mount
  useEffect(() => {
    fetchSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One-time URL → store sync (only on initial load, not on subsequent URL changes)
  const initialSynced = useRef(false);
  useEffect(() => {
    if (!initialSynced.current && sessionIdFromUrl) {
      initialSynced.current = true;
      selectSession(sessionIdFromUrl);
    }
  }, [sessionIdFromUrl, selectSession]);

  // Sync store → URL (one-way: store is the source of truth)
  useEffect(() => {
    if (currentSessionId && currentSessionId !== sessionIdFromUrl) {
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
    clearSelection();
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
    clearSelection,
    fetchMessages,
    fetchRuntime,
    fetchPendingPermissions,
    fetchPolicy,
    fetchTree,
    fetchTaskPlan,
    clearTaskPlan,
  ]);

  // Status badge color + user-friendly labels
  const statusColor: Record<string, string> = {
    idle: "bg-bg-tertiary text-text-secondary",
    queued: "bg-yellow-500/20 text-yellow-400",
    running: "bg-accent/20 text-accent",
    waiting: "bg-yellow-500/20 text-yellow-500",
    error: "bg-danger/20 text-danger",
  };
  const statusLabel: Record<string, string> = {
    idle: "Idle",
    queued: "Queued",
    running: "Running",
    waiting: "Waiting",
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

        {/* Input */}
        <PromptInput sessionId={currentSessionId} />
      </div>

      {/* File preview panel (shown when a file is selected) */}
      {selectedFile && (
        <div className="w-80 shrink-0">
          <FilePreview sessionId={currentSessionId} />
        </div>
      )}
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
