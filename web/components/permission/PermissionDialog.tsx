"use client";

import { useState } from "react";
import { apiFetch, ApiRequestError } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { useSessionStore } from "@/store/session";
import type { SSEPermissionAsk } from "@/lib/types";
import {
  ShieldAlert,
  CheckCircle,
  CheckCheck,
  XCircle,
  Terminal,
  FilePen,
  Code,
} from "lucide-react";

const toolIcons: Record<string, React.ElementType> = {
  bash: Terminal,
  file_write: FilePen,
  script: Code,
};

interface PermissionDialogProps {
  permissions: SSEPermissionAsk[];
  sessionId: string;
}

export default function PermissionDialog({
  permissions,
  sessionId,
}: PermissionDialogProps) {
  const [processing, setProcessing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const removePendingPermission = useChatStore((s) => s.removePendingPermission);
  const fetchPendingPermissions = useChatStore((s) => s.fetchPendingPermissions);
  const fetchRuntime = useChatStore((s) => s.fetchRuntime);
  const fetchMessages = useChatStore((s) => s.fetchMessages);
  const setStatus = useChatStore((s) => s.setStatus);
  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);

  const syncAfterResolve = async (permissionId: string) => {
    removePendingPermission(permissionId);
    const runtime = await fetchRuntime(sessionId);
    if (runtime) {
      setStatus(runtime.status);
      updateSessionStatus(sessionId, runtime.status);
    }
    await fetchPendingPermissions(sessionId);
    await fetchMessages(sessionId);
  };

  const handleApprove = async (
    permissionId: string,
    mode: "once" | "always",
  ) => {
    setProcessing(permissionId);
    setError(null);
    try {
      await apiFetch(`/permissions/${permissionId}/approve`, {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      await syncAfterResolve(permissionId);
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? err.message : "操作失败";
      setError(msg);
    } finally {
      setProcessing(null);
    }
  };

  const handleDeny = async (permissionId: string) => {
    setProcessing(permissionId);
    setError(null);
    try {
      await apiFetch(`/permissions/${permissionId}/deny`, {
        method: "POST",
      });
      await syncAfterResolve(permissionId);
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? err.message : "操作失败";
      setError(msg);
    } finally {
      setProcessing(null);
    }
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-30 flex justify-center px-4">
      <div className="pointer-events-auto w-full max-w-[980px] rounded-[24px] bg-bg-primary/96 px-4 py-3 shadow-[0_20px_40px_rgba(42,41,51,0.12)] backdrop-blur">
      <div className="mb-1 flex items-center gap-2 text-sm font-medium text-warning-foreground">
        <ShieldAlert size={16} />
        待确认操作
      </div>
      <p className="mb-2 text-xs text-text-secondary">
        AgentD 正在等待你的授权确认。
      </p>

      {error && (
        <div className="mb-2 rounded-[14px] bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {permissions.map((p) => {
          const Icon = toolIcons[p.tool_name] || Terminal;
          const isProcessing = processing === p.permission_id;

          const inputSummary =
            p.tool_name === "bash"
              ? (p.input.command as string)
              : p.tool_name === "file_write"
                ? (p.input.path as string)
                : JSON.stringify(p.input);

          return (
            <div
              key={p.permission_id}
              className="rounded-[18px] bg-white/70 px-3 py-3"
            >
              <div className="flex items-center gap-3">
                <Icon size={16} className="shrink-0 text-text-secondary" />
                <div className="min-w-0 flex-1">
                  <span className="text-[11px] font-medium text-text-secondary">
                    {p.tool_name}
                  </span>
                  <p className="truncate text-[13px] text-text-primary">
                    {inputSummary}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={() => handleApprove(p.permission_id, "once")}
                  disabled={isProcessing}
                  className="flex h-8 items-center justify-center gap-1 rounded-[12px] bg-success/14 px-3 text-xs text-success transition hover:bg-success/22 disabled:opacity-50"
                >
                  <CheckCircle size={12} />
                  仅本次
                </button>
                <button
                  onClick={() => handleApprove(p.permission_id, "always")}
                  disabled={isProcessing}
                  className="flex h-8 items-center justify-center gap-1 rounded-[12px] bg-accent/14 px-3 text-xs text-accent transition hover:bg-accent/22 disabled:opacity-50"
                >
                  <CheckCheck size={12} />
                  始终允许
                </button>
                <button
                  onClick={() => handleDeny(p.permission_id)}
                  disabled={isProcessing}
                  className="flex h-8 items-center justify-center gap-1 rounded-[12px] bg-danger/14 px-3 text-xs text-danger transition hover:bg-danger/22 disabled:opacity-50"
                >
                  <XCircle size={12} />
                  拒绝
                </button>
              </div>
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}
