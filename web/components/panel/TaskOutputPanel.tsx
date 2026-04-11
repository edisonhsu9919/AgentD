"use client";

import { useEffect, useRef, useState } from "react";
import { usePanelStore } from "@/store/panel";
import { useWorkspaceStore } from "@/store/workspace";
import type { TaskInstance } from "@/lib/types";
import {
  Terminal,
  CheckCircle,
  XCircle,
  Loader2,
  FolderOpen,
  FileText,
  ExternalLink,
  Cpu,
  GitBranch,
  Clock,
  Ban,
} from "lucide-react";
import Link from "next/link";

interface TaskOutputPanelProps {
  sessionId: string;
}

/** Polling interval (ms) for live task state + stdout refresh */
const TASK_POLL_INTERVAL = 2000;

/** Task status values that indicate ongoing work worth polling for */
const ACTIVE_STATUSES = new Set(["queued", "running", "waiting"]);

export default function TaskOutputPanel({ sessionId }: TaskOutputPanelProps) {
  const taskList = usePanelStore((s) => s.taskList);
  const activeTaskId = usePanelStore((s) => s.activeTaskId);
  const selectTask = usePanelStore((s) => s.selectTask);
  const fetchTasks = usePanelStore((s) => s.fetchTasks);
  const fetchTaskStdout = usePanelStore((s) => s.fetchTaskStdout);

  // Reconcile task list from API when panel opens
  useEffect(() => {
    fetchTasks(sessionId);
  }, [sessionId, fetchTasks]);

  // Fetch stdout when active task changes
  useEffect(() => {
    if (activeTaskId) {
      fetchTaskStdout(sessionId, activeTaskId);
    }
  }, [activeTaskId, sessionId, fetchTaskStdout]);

  // F1 + F2: Poll task list + active stdout as SSE fallback.
  // Polling stops once all tasks are settled (completed/failed/cancelled),
  // ensuring both live log refresh and final-state reconcile.
  const hasActiveTask = taskList.some((t) => ACTIVE_STATUSES.has(t.status));
  // Track whether we just transitioned from active → all settled, so we
  // can do one final reconcile pull to make sure UI matches API truth.
  const wasActiveRef = useRef(false);

  useEffect(() => {
    if (!hasActiveTask) {
      // One final reconcile after the last task settled
      if (wasActiveRef.current) {
        wasActiveRef.current = false;
        fetchTasks(sessionId);
        if (activeTaskId) {
          fetchTaskStdout(sessionId, activeTaskId);
        }
      }
      return;
    }

    wasActiveRef.current = true;
    const interval = setInterval(() => {
      fetchTasks(sessionId);
      if (activeTaskId) {
        fetchTaskStdout(sessionId, activeTaskId);
      }
    }, TASK_POLL_INTERVAL);

    return () => clearInterval(interval);
  }, [hasActiveTask, sessionId, activeTaskId, fetchTasks, fetchTaskStdout]);

  const activeTask = taskList.find((t) => t.task_id === activeTaskId);

  if (taskList.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <Terminal size={28} className="text-text-secondary/30" />
        <div>
          <p className="text-sm font-medium text-text-secondary">
            Task Output
          </p>
          <p className="mt-1 max-w-[240px] text-xs text-text-secondary/60">
            Background tasks and script output will appear here when the agent
            launches long-running processes.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Left: Task list */}
      <div className="w-48 shrink-0 overflow-y-auto border-r border-border bg-bg-primary/30">
        <div className="px-2 py-1.5">
          <span className="text-[10px] font-medium text-text-secondary">
            Tasks ({taskList.length})
          </span>
        </div>
        {taskList.map((task) => (
          <button
            key={task.task_id}
            onClick={() => selectTask(task.task_id)}
            className={`flex w-full items-center gap-2 px-2 py-1.5 text-left transition ${
              task.task_id === activeTaskId
                ? "bg-bg-tertiary text-text-primary"
                : "text-text-secondary hover:bg-bg-tertiary/50"
            }`}
          >
            <TaskStatusIcon status={task.status} size={13} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">
                {task.title || "Untitled"}
              </div>
              <div className="flex items-center gap-1 text-[10px] text-text-secondary">
                <TaskKindBadge kind={task.task_kind} />
              </div>
            </div>
          </button>
        ))}
      </div>

      {/* Right: Active task detail */}
      <div className="flex min-w-0 flex-1 flex-col">
        {activeTask ? (
          <TaskDetail task={activeTask} sessionId={sessionId} />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-text-secondary">
            Select a task to view details
          </div>
        )}
      </div>
    </div>
  );
}

// --- Task Detail ---

function TaskDetail({ task, sessionId }: { task: TaskInstance; sessionId: string }) {
  const taskOutputLogs = usePanelStore((s) => s.taskOutputLogs);
  const openFilePreview = usePanelStore((s) => s.openFilePreview);
  const stopTask = usePanelStore((s) => s.stopTask);
  const fileTree = useWorkspaceStore((s) => s.fileTree);
  const logEndRef = useRef<HTMLDivElement>(null);
  const [stopping, setStopping] = useState(false);

  const lines = taskOutputLogs[task.task_id] || [];

  // Auto-scroll to bottom on new lines
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  return (
    <div className="flex h-full flex-col">
      {/* Status bar */}
      <div className="space-y-1.5 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <TaskStatusIcon status={task.status} size={14} />
          <span className="text-xs font-medium text-text-primary">
            {task.title || "Untitled Task"}
          </span>
          <TaskKindBadge kind={task.task_kind} />
          <TaskStatusBadge status={task.status} />
        </div>

        <div className="flex flex-wrap gap-x-4 text-[10px] text-text-secondary">
          {task.pid && (
            <span>PID: {task.pid}</span>
          )}
          {task.command && (
            <span className="max-w-[200px] truncate font-mono" title={task.command}>
              {task.command}
            </span>
          )}
          <span>
            {new Date(task.created_at).toLocaleTimeString()}
          </span>
        </div>

        {/* Child session link */}
        {task.task_kind === "child_session" && task.child_session_id && (
          <Link
            href={`/chat?s=${task.child_session_id}`}
            className="inline-flex items-center gap-1 text-[10px] text-accent transition hover:underline"
          >
            <ExternalLink size={10} />
            View child session
          </Link>
        )}

        {/* Stop button — only for running detached process tasks */}
        {task.task_kind === "process" && task.status === "running" && (
          <button
            onClick={async () => {
              if (!window.confirm("Stop this background task?")) return;
              setStopping(true);
              try {
                await stopTask(sessionId, task.task_id);
              } catch {
                // ignore
              } finally {
                setStopping(false);
              }
            }}
            disabled={stopping}
            className="inline-flex items-center gap-1 rounded bg-danger/10 px-2 py-1 text-[10px] font-medium text-danger transition hover:bg-danger/20 disabled:opacity-50"
          >
            {stopping ? <Loader2 size={10} className="animate-spin" /> : <Ban size={10} />}
            {stopping ? "Stopping..." : "Stop Task"}
          </button>
        )}

        {/* Error message */}
        {task.error && (
          <div className="rounded bg-danger/10 px-2 py-1 text-[10px] text-danger">
            {task.error}
          </div>
        )}
      </div>

      {/* Log area */}
      <div className="flex-1 overflow-auto bg-bg-primary/30 p-3 font-mono text-xs">
        {lines.length === 0 ? (
          <div className="flex h-full items-center justify-center text-text-secondary/50">
            <div className="text-center">
              <Terminal size={20} className="mx-auto mb-2" />
              <p>
                {task.status === "running"
                  ? "Waiting for output..."
                  : task.status === "completed"
                    ? "Task completed (no captured output)"
                    : "No output yet"}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {lines.map((line, i) => (
              <div key={i} className="text-text-primary whitespace-pre-wrap break-all">
                {line}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}
      </div>

      {/* Result summary */}
      {task.result_summary && (
        <div className="border-t border-border px-3 py-2">
          <div className="mb-1 text-[10px] font-medium text-text-secondary">Result</div>
          <p className="text-xs text-text-primary">{task.result_summary}</p>
        </div>
      )}
    </div>
  );
}

// --- Shared components ---

function TaskStatusIcon({ status, size = 14 }: { status: string; size?: number }) {
  switch (status) {
    case "running":
    case "waiting":
      return <Loader2 size={size} className="animate-spin text-accent" />;
    case "completed":
      return <CheckCircle size={size} className="text-success" />;
    case "failed":
      return <XCircle size={size} className="text-danger" />;
    case "cancelled":
      return <Ban size={size} className="text-text-secondary" />;
    case "queued":
      return <Clock size={size} className="text-yellow-400" />;
    default:
      return <Terminal size={size} className="text-text-secondary" />;
  }
}

function TaskKindBadge({ kind }: { kind: string }) {
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium ${
        kind === "child_session"
          ? "bg-purple-500/10 text-purple-400"
          : "bg-blue-500/10 text-blue-400"
      }`}
    >
      {kind === "child_session" ? (
        <><GitBranch size={8} /> subagent</>
      ) : (
        <><Cpu size={8} /> process</>
      )}
    </span>
  );
}

function TaskStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    queued: "bg-yellow-500/10 text-yellow-400",
    running: "bg-accent/10 text-accent",
    waiting: "bg-yellow-500/10 text-yellow-500",
    completed: "bg-success/10 text-success",
    failed: "bg-danger/10 text-danger",
    cancelled: "bg-bg-tertiary text-text-secondary",
  };

  return (
    <span className={`rounded px-1 py-0.5 text-[9px] font-medium ${colors[status] || ""}`}>
      {status}
    </span>
  );
}
