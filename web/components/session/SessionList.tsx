"use client";

import { useSessionStore } from "@/store/session";
import { useChatStore } from "@/store/chat";
import { stripThinkTags } from "@/lib/constants";
import { showToast } from "@/components/ui/Toast";
import { Plus, Loader2, Trash2, MessageSquare, Cpu } from "lucide-react";
import type { Session, SessionStatus } from "@/lib/types";

const statusColors: Record<SessionStatus, string> = {
  idle: "bg-text-secondary",
  queued: "bg-yellow-400",
  running: "bg-accent",
  waiting: "bg-yellow-500",
  subtask_waiting: "bg-purple-400",
  error: "bg-danger",
};

function SessionItem({
  session,
  isActive,
  hasDetachedTasks,
  onSelect,
  onDelete,
}: {
  session: Session;
  isActive: boolean;
  hasDetachedTasks: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      className={`group flex cursor-pointer items-center gap-2 rounded px-3 py-2 transition ${
        isActive
          ? "bg-bg-tertiary text-text-primary"
          : "text-text-secondary hover:bg-bg-tertiary/50"
      }`}
    >
      <MessageSquare size={14} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate text-sm">{stripThinkTags(session.title)}</span>
      {hasDetachedTasks && (
        <span
          className="flex shrink-0 items-center gap-0.5 rounded bg-purple-500/10 px-1 py-0.5 text-[9px] font-medium text-purple-400"
          title="Background tasks running"
        >
          <Cpu size={9} />
        </span>
      )}
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${statusColors[session.status]}`}
        title={session.status}
      />
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="shrink-0 opacity-0 transition group-hover:opacity-100 hover:text-danger"
        title="Delete session"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

export default function SessionList() {
  const { sessions, currentSessionId, isCreating, selectSession, createAndSelectSession, deleteSession } =
    useSessionStore();
  const runtime = useChatStore((s) => s.runtime);

  return (
    <div className="flex flex-col gap-1">
      <button
        onClick={() => createAndSelectSession()}
        disabled={isCreating}
        className="flex items-center gap-2 rounded px-3 py-2 text-sm text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-50"
      >
        {isCreating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
        {isCreating ? "Creating..." : "New Session"}
      </button>

      <div className="mt-1 flex flex-col gap-0.5">
        {sessions.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            isActive={s.id === currentSessionId}
            hasDetachedTasks={
              s.id === currentSessionId &&
              !!runtime?.has_running_detached_tasks
            }
            onSelect={() => selectSession(s.id)}
            onDelete={() => deleteSession(s.id).catch((e: Error) => showToast("error", e.message))}
          />
        ))}
      </div>
    </div>
  );
}
