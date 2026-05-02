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
import CompactBanner from "@/components/chat/CompactBanner";
import PanelShell from "@/components/panel/PanelShell";
import { usePanelStore } from "@/store/panel";
import AgentDRunningMark from "@/components/brand/AgentDRunningMark";
import { PanelRight } from "lucide-react";

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
  } = useChatStore();
  const pendingPermissions = useChatStore((s) => s.pendingPermissions);
  const chatStatus = useChatStore((s) => s.status);
  const runtime = useChatStore((s) => s.runtime);
  const contextWarning = useChatStore((s) => s.contextWarning);
  const justCompacted = useChatStore((s) => s.justCompacted);

  const fetchTree = useWorkspaceStore((s) => s.fetchTree);

  const clearPanel = usePanelStore((s) => s.clearPanel);
  const fetchTasks = usePanelStore((s) => s.fetchTasks);
  const togglePanel = usePanelStore((s) => s.togglePanel);
  const panelOpen = usePanelStore((s) => s.open);

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

      // 3. If runtime explicitly says permission_waiting, recover pending permissions.
      // Other waiting states (notably subtask_waiting) must not show permission recovery.
      if (runtime && runtime.phase === "permission_waiting") {
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
    idle: "bg-bg-primary text-text-secondary",
    queued: "bg-warning/80 text-warning-foreground",
    running: "bg-accent/12 text-accent",
    waiting: "bg-warning/80 text-warning-foreground",
    subtask_waiting: "bg-purple-500/14 text-purple-500",
    error: "bg-danger/12 text-danger",
  };
  const statusLabel: Record<string, string> = {
    idle: "待命",
    queued: "排队中",
    running: "执行中",
    waiting: "待授权",
    subtask_waiting: "等待子任务",
    error: "异常",
  };

  const currentSession = sessions.find((s) => s.id === currentSessionId);

  // Only show spinner before we have a session. Once currentSessionId is set,
  // background session-list refreshes must NOT unmount the chat area.
  if (!currentSessionId) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <div className="surface-card flex w-full max-w-md flex-col items-center gap-4 px-8 py-10 text-center">
          <AgentDRunningMark size={26} />
          <div className="space-y-2">
            <div className="font-caption text-[11px] tracking-[0.12em] text-text-secondary">
              正在准备工作台
            </div>
            <p className="text-sm leading-7 text-text-secondary">
              正在恢复会话与运行时状态，请稍候。
            </p>
          </div>
        </div>
      </div>
    );
  }

  const sessionTitle = stripThinkTags(currentSession?.title || "未命名会话");

  return (
    <div className="flex h-full min-w-0 flex-1 overflow-hidden bg-transparent">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-4 py-3 md:px-6 md:py-4">
        <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 px-2 py-2 md:px-4 md:py-2.5">
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <h2 className="truncate text-[16px] font-medium tracking-[-0.02em] text-text-primary">
                  {sessionTitle}
                </h2>
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${statusColor[chatStatus] || statusColor.idle}`}
                >
                  {statusLabel[chatStatus] || chatStatus}
                </span>
                <SessionStatusIndicator />
              </div>
            </div>

            <div className="shrink-0">
              <div className="group relative z-40">
                <button
                  onClick={togglePanel}
                  className={`inline-flex h-9 w-9 items-center justify-center rounded-full text-text-secondary transition hover:bg-bg-primary hover:text-text-primary ${panelOpen ? "bg-accent/10 text-accent" : ""}`}
                  title={panelOpen ? "收起面板" : "展开面板"}
                >
                  <PanelRight size={15} />
                </button>
                <div className="ui-tooltip pointer-events-none absolute right-0 top-[calc(100%+0.5rem)] z-[80] hidden whitespace-nowrap group-hover:block">
                  {panelOpen ? "收起面板" : "展开面板"}
                </div>
              </div>
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
              <TaskPlanPanel />

              <MessageList />

              {pendingPermissions.length > 0 && (
                <PermissionDialog
                  permissions={pendingPermissions}
                  sessionId={currentSessionId}
                />
              )}

            {/* Waiting recovery: session is permission_waiting but SSE permission_ask was missed.
                Only show after session enter completes (sessionReady) to avoid flashing
                the banner while fetchPendingPermissions is still in-flight. */}
            {sessionReady &&
              runtime?.phase === "permission_waiting" &&
              pendingPermissions.length === 0 && (
                <WaitingRecoveryBanner sessionId={currentSessionId} />
              )}

              {(chatStatus === "subtask_waiting" || runtime?.phase === "subtask_waiting") && (
                <div className="flex items-center gap-2 border-t border-purple-500/20 bg-purple-500/6 px-4 py-3">
                  <div className="h-2 w-2 animate-pulse rounded-full bg-purple-500" />
                  <span className="text-xs text-purple-500">
                    正在等待子任务完成，相关输出会继续汇入右侧工作面板。
                  </span>
                  <button
                    onClick={() => {
                      const { openTaskOutput } = usePanelStore.getState();
                      openTaskOutput();
                    }}
                    className="ml-auto rounded-full bg-purple-500/14 px-3 py-1 text-[11px] font-medium text-purple-500 transition hover:bg-purple-500/20"
                  >
                    查看任务输出
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
            </div>

            <div className="relative z-[60] shrink-0">
              <PromptInput
                sessionId={currentSessionId}
                contextUsageRatio={runtime?.context_usage_ratio}
                promptTokens={runtime?.last_call_prompt_tokens}
                windowLimit={runtime?.context_window_limit}
              />
            </div>
          </div>
        </section>
      </div>

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
