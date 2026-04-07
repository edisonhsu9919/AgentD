"use client";

import { useEffect, useRef, useCallback } from "react";
import { connectSSE, disconnectSSE } from "@/lib/sse";
import { useChatStore } from "@/store/chat";
import { useSessionStore } from "@/store/session";
import { useWorkspaceStore } from "@/store/workspace";
import { useTaskPlanStore } from "@/store/taskPlan";
import { usePanelStore } from "@/store/panel";
import { showToast } from "@/components/ui/Toast";
import type { SSEEvent } from "@/lib/types";

/** Interval (ms) for polling runtime when status is queued/running */
const WATCHDOG_INTERVAL = 5000;

/** Typing effect: interval between render ticks (ms) */
const TYPING_TICK_MS = 25;
/** Typing effect: chars per tick when buffer is small (< threshold) */
const TYPING_CHARS_SLOW = 2;
/** Typing effect: chars per tick when buffer is large (>= threshold) */
const TYPING_CHARS_FAST = 5;
/** Buffer length threshold to switch from slow to fast */
const TYPING_SPEED_THRESHOLD = 60;

export function useSSE(sessionId: string | null) {
  const prevId = useRef<string | null>(null);

  // --- Typing effect buffer (ref-based, no re-render on append) ---
  const bufferRef = useRef("");
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const appendStreamingDraft = useChatStore((s) => s.appendStreamingDraft);
  const appendStreamingThinking = useChatStore(
    (s) => s.appendStreamingThinking,
  );
  const addStreamingToolCall = useChatStore((s) => s.addStreamingToolCall);
  const updateStreamingToolResult = useChatStore(
    (s) => s.updateStreamingToolResult,
  );
  const clearStreaming = useChatStore((s) => s.clearStreaming);
  const setStatus = useChatStore((s) => s.setStatus);
  const fetchMessages = useChatStore((s) => s.fetchMessages);
  const fetchRuntime = useChatStore((s) => s.fetchRuntime);
  const addPendingPermission = useChatStore((s) => s.addPendingPermission);
  const removePendingPermission = useChatStore(
    (s) => s.removePendingPermission,
  );
  const clearPendingPermissions = useChatStore(
    (s) => s.clearPendingPermissions,
  );

  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);
  const updateSessionTitle = useSessionStore((s) => s.updateSessionTitle);
  const fetchSessions = useSessionStore((s) => s.fetchSessions);

  const setContextWarning = useChatStore((s) => s.setContextWarning);
  const clearJustCompacted = useChatStore((s) => s.clearJustCompacted);

  const fetchTree = useWorkspaceStore((s) => s.fetchTree);
  const fetchTaskPlan = useTaskPlanStore((s) => s.fetchTaskPlan);

  const setPanelContent = usePanelStore((s) => s.setPanelContent);
  const addTask = usePanelStore((s) => s.addTask);
  const updateTaskStatus = usePanelStore((s) => s.updateTaskStatus);
  const appendTaskLog = usePanelStore((s) => s.appendTaskLog);
  const fetchTasksFromAPI = usePanelStore((s) => s.fetchTasks);

  // --- Typing loop helpers ---

  const stopTypingLoop = useCallback(() => {
    if (typingTimerRef.current) {
      clearInterval(typingTimerRef.current);
      typingTimerRef.current = null;
    }
  }, []);

  /** Flush all remaining buffer to streamingDraft immediately */
  const flushBuffer = useCallback(() => {
    stopTypingLoop();
    if (bufferRef.current.length > 0) {
      appendStreamingDraft(bufferRef.current);
      bufferRef.current = "";
    }
  }, [stopTypingLoop, appendStreamingDraft]);

  /** Clear buffer without flushing (discard) */
  const clearBuffer = useCallback(() => {
    stopTypingLoop();
    bufferRef.current = "";
  }, [stopTypingLoop]);

  /** Start the typing render loop if not already running */
  const startTypingLoop = useCallback(() => {
    if (typingTimerRef.current) return;
    typingTimerRef.current = setInterval(() => {
      const buf = bufferRef.current;
      if (buf.length === 0) {
        stopTypingLoop();
        return;
      }
      // Adaptive speed: go faster when buffer is large to avoid falling behind
      const charsPerTick =
        buf.length >= TYPING_SPEED_THRESHOLD
          ? TYPING_CHARS_FAST
          : TYPING_CHARS_SLOW;
      const chunk = buf.slice(0, charsPerTick);
      bufferRef.current = buf.slice(charsPerTick);
      appendStreamingDraft(chunk);
    }, TYPING_TICK_MS);
  }, [stopTypingLoop, appendStreamingDraft]);

  // --- SSE connection ---

  useEffect(() => {
    if (!sessionId) {
      disconnectSSE();
      clearBuffer();
      return;
    }

    // Same session — skip reconnect
    if (prevId.current === sessionId) return;
    prevId.current = sessionId;

    const handleEvent = (event: SSEEvent) => {
      switch (event.event) {
        case "text_delta":
          // Append to buffer (no re-render); typing loop will flush gradually
          bufferRef.current += event.content;
          startTypingLoop();
          break;

        case "reasoning_delta":
          appendStreamingThinking(event.content);
          break;

        case "tool_start":
          addStreamingToolCall({
            tool_call_id: event.tool_call_id,
            tool_name: event.tool_name,
            input: event.input,
            status: "running",
          });
          break;

        case "tool_result":
          updateStreamingToolResult(
            event.tool_call_id,
            event.output,
            event.is_error,
          );
          // Refresh task plan when planning/todo_update tools complete
          if (
            event.tool_name === "planning" ||
            event.tool_name === "todo_update"
          ) {
            fetchTaskPlan(sessionId);
          }
          // Refresh file tree when file-modifying tools complete
          if (
            event.tool_name === "file_write" ||
            event.tool_name === "file_edit" ||
            event.tool_name === "bash"
          ) {
            fetchTree(sessionId);
          }
          // Auto-open Task Output when a background task launches successfully
          if (
            (event.tool_name === "launch_detached_process" ||
              event.tool_name === "launch_subagent") &&
            !event.is_error
          ) {
            try {
              const output = JSON.parse(event.output);
              if (
                output.status === "launched" ||
                output.status === "waiting_for_child"
              ) {
                const ps = usePanelStore.getState();
                if (!ps.open) {
                  ps.openTaskOutput();
                } else if (ps.activeType !== "task_output") {
                  ps.setTabAttention("task_output", true);
                }
                // Immediately reconcile task list so child session entry is visible
                fetchTasksFromAPI(sessionId);
              }
            } catch {
              // output parse failed — ignore
            }
          }
          break;

        case "status_change":
          setStatus(event.status);
          updateSessionStatus(sessionId, event.status);
          break;

        case "title_update":
          updateSessionTitle(sessionId, event.title);
          break;

        case "permission_ask":
          addPendingPermission(event);
          break;

        case "permission_resolved":
          removePendingPermission(event.permission_id);
          break;

        case "done":
          // Flush any remaining typing buffer immediately so no chars are lost
          flushBuffer();
          clearPendingPermissions();
          // Immediately mark as idle — done is the definitive terminal signal.
          // Don't rely on a prior status_change SSE or async fetchRuntime.
          setStatus("idle");
          updateSessionStatus(sessionId, "idle");
          // A real model call completed — runtime ratio is now fresh,
          // so clear the justCompacted suppression flag.
          clearJustCompacted();
          // Reconcile task list from API after run completes
          fetchTasksFromAPI(sessionId);
          // Keep streaming content visible until persisted messages arrive
          fetchMessages(sessionId).finally(() => {
            clearStreaming();
          });
          fetchSessions();
          fetchRuntime(sessionId);
          fetchTree(sessionId);
          fetchTaskPlan(sessionId);
          break;

        case "context_warning":
          // New warning from backend overrides justCompacted suppression
          clearJustCompacted();
          setContextWarning(true);
          break;

        case "compaction_done":
          setContextWarning(false);
          fetchMessages(sessionId);
          fetchRuntime(sessionId);
          showToast("info", `Context compacted — saved ${event.tokens_saved.toLocaleString()} tokens`);
          break;

        case "panel_update":
          setPanelContent(event.panel_type, event.panel_content);
          break;

        case "panel_submit":
          // Backend confirms receipt — no action needed, HtmlAppPanel handles UI
          break;

        case "task_started":
          addTask({
            task_id: event.task_id,
            session_id: sessionId,
            task_kind: event.task_kind,
            blocking_mode: event.task_kind === "child_session" ? "blocking" : "detached",
            status: event.status === "waiting_for_child" ? "waiting" : "running",
            title: event.task_id,
            command: "",
            spawned_by_tool: "",
            tool_call_id: "",
            child_session_id: event.child_session_id || null,
            pid: null,
            artifact_root: "",
            stdout_path: "",
            stderr_path: "",
            created_at: event.timestamp,
            updated_at: event.timestamp,
          });
          break;

        case "task_completed":
          updateTaskStatus(event.task_id, "completed");
          break;

        case "task_failed":
          updateTaskStatus(event.task_id, "failed");
          break;

        case "error": {
          console.error("[SSE error]", event.code, event.message);
          // Flush buffer before showing error (don't lose partial text)
          flushBuffer();
          const errMsg = event.message || "Agent error occurred";
          const displayMsg = errMsg.length > 120
            ? errMsg.slice(0, 120) + "..."
            : errMsg;
          showToast("error", `[${event.code}] ${displayMsg}`);
          setStatus("error");
          break;
        }
      }
    };

    connectSSE(sessionId, {
      onEvent: handleEvent,
      onError: (err) => {
        console.error("[SSE connection error]", err);
        showToast("error", "SSE connection lost. Reconnecting...");
      },
    });

    return () => {
      disconnectSSE();
      clearBuffer();
      prevId.current = null;
    };
  }, [
    sessionId,
    appendStreamingDraft,
    appendStreamingThinking,
    addStreamingToolCall,
    updateStreamingToolResult,
    clearStreaming,
    clearPendingPermissions,
    setStatus,
    fetchMessages,
    fetchRuntime,
    addPendingPermission,
    removePendingPermission,
    updateSessionStatus,
    updateSessionTitle,
    fetchSessions,
    fetchTree,
    fetchTaskPlan,
    setContextWarning,
    clearJustCompacted,
    setPanelContent,
    addTask,
    updateTaskStatus,
    appendTaskLog,
    fetchTasksFromAPI,
    startTypingLoop,
    flushBuffer,
    clearBuffer,
  ]);

  // ---------------------------------------------------------------
  // Watchdog: poll runtime when status is queued/running to recover
  // from missed SSE events (especially under concurrent load).
  // ---------------------------------------------------------------
  const reconcileWithTruth = useCallback(async () => {
    if (!sessionId) return;
    const status = useChatStore.getState().status;
    if (status !== "queued" && status !== "running") return;

    const runtime = await fetchRuntime(sessionId);
    if (!runtime) return;

    // Backend has moved on but frontend is stuck — reconcile
    if (runtime.status === "idle" || runtime.status === "error") {
      clearBuffer();
      clearStreaming();
      clearPendingPermissions();
      fetchMessages(sessionId);
      fetchSessions();
      fetchTree(sessionId);
      fetchTaskPlan(sessionId);
    } else if (runtime.status === "waiting") {
      const { fetchPendingPermissions } = useChatStore.getState();
      await fetchPendingPermissions(sessionId);
    }
  }, [sessionId, fetchRuntime, clearBuffer, clearStreaming, clearPendingPermissions, fetchMessages, fetchSessions, fetchTree, fetchTaskPlan]);

  useEffect(() => {
    if (!sessionId) return;

    const timer = setInterval(reconcileWithTruth, WATCHDOG_INTERVAL);
    return () => clearInterval(timer);
  }, [sessionId, reconcileWithTruth]);
}
