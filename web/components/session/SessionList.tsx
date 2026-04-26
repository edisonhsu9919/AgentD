"use client";

import { useSessionStore } from "@/store/session";
import { useChatStore } from "@/store/chat";
import { stripThinkTags } from "@/lib/constants";
import { showToast } from "@/components/ui/Toast";
import {
  Loader2,
  MessageSquarePlus,
  Trash2,
  Cpu,
} from "lucide-react";
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
      className="group flex cursor-pointer items-center gap-2 px-1 py-0.5 transition"
    >
      <div className="min-w-0 flex flex-1 items-center gap-2">
        {session.status !== "idle" && (
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${statusColors[session.status]}`}
            title={session.status}
          />
        )}
        <div
          className={`min-w-0 flex-1 rounded-[12px] px-2.5 py-1.5 transition ${
            isActive
              ? "bg-accent/10 text-text-primary"
              : "text-text-secondary group-hover:bg-bg-primary/70 group-hover:text-text-primary"
          }`}
        >
          <span className="block truncate text-sm">
            {stripThinkTags(session.title)}
          </span>
        </div>
      </div>
      {hasDetachedTasks && (
        <span
          className="flex shrink-0 items-center gap-1 text-[10px] font-medium text-purple-500"
          title="后台任务运行中"
        >
          <Cpu size={10} />
          任务中
        </span>
      )}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="shrink-0 rounded-full p-1 opacity-0 transition group-hover:opacity-100 hover:bg-danger/8 hover:text-danger"
        title="删除会话"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

export default function SessionList() {
  const { sessions, currentSessionId, isCreating, selectSession, createAndSelectSession, deleteSession } =
    useSessionStore();
  const runtime = useChatStore((s) => s.runtime);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="mb-3 flex items-center justify-end px-1">
        <button
          onClick={() => createAndSelectSession()}
          disabled={isCreating}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-secondary transition hover:bg-bg-tertiary/50 hover:text-text-primary disabled:opacity-50"
          title="新建会话"
        >
          {isCreating ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <MessageSquarePlus size={14} />
          )}
          新建会话
        </button>
      </div>

      <div className="flex flex-col gap-0.5">
        {sessions.length === 0 ? (
          <div className="px-1 py-3 text-sm text-text-secondary">
            还没有会话，先创建一个新的工作会话。
          </div>
        ) : (
          sessions.map((s) => (
            <SessionItem
              key={s.id}
              session={s}
              isActive={s.id === currentSessionId}
              hasDetachedTasks={
                s.id === currentSessionId &&
                !!runtime?.has_running_detached_tasks
              }
              onSelect={() => selectSession(s.id)}
              onDelete={() =>
                deleteSession(s.id).catch((e: Error) =>
                  showToast("error", e.message),
                )
              }
            />
          ))
        )}
      </div>
    </div>
  );
}
