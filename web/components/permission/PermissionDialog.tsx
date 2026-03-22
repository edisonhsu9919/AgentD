"use client";

import { useState } from "react";
import { apiFetch, ApiRequestError } from "@/lib/api";
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
}: PermissionDialogProps) {
  const [processing, setProcessing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? err.message : "Action failed";
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
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? err.message : "Action failed";
      setError(msg);
    } finally {
      setProcessing(null);
    }
  };

  return (
    <div className="flow-border border-t border-yellow-500/30 bg-yellow-500/5 px-4 py-3">
      <div className="mb-1 flex items-center gap-2 text-sm font-medium text-yellow-500">
        <ShieldAlert size={16} />
        Permission Required
      </div>
      <p className="mb-2 text-xs text-text-secondary">
        Agent is paused until approval
      </p>

      {error && (
        <div className="mb-2 rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
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
              className="rounded border border-border bg-bg-secondary px-3 py-2"
            >
              <div className="flex items-center gap-3">
                <Icon size={16} className="shrink-0 text-text-secondary" />
                <div className="min-w-0 flex-1">
                  <span className="text-xs font-medium text-text-secondary">
                    {p.tool_name}
                  </span>
                  <p className="truncate text-sm text-text-primary">
                    {inputSummary}
                  </p>
                </div>
              </div>
              <div className="mt-2 flex gap-1.5">
                <button
                  onClick={() => handleApprove(p.permission_id, "once")}
                  disabled={isProcessing}
                  className="flex items-center gap-1 rounded bg-success/20 px-2.5 py-1 text-xs text-success transition hover:bg-success/30 disabled:opacity-50"
                >
                  <CheckCircle size={12} />
                  Approve Once
                </button>
                <button
                  onClick={() => handleApprove(p.permission_id, "always")}
                  disabled={isProcessing}
                  className="flex items-center gap-1 rounded bg-accent/20 px-2.5 py-1 text-xs text-accent transition hover:bg-accent/30 disabled:opacity-50"
                >
                  <CheckCheck size={12} />
                  Approve Always
                </button>
                <button
                  onClick={() => handleDeny(p.permission_id)}
                  disabled={isProcessing}
                  className="flex items-center gap-1 rounded bg-danger/20 px-2.5 py-1 text-xs text-danger transition hover:bg-danger/30 disabled:opacity-50"
                >
                  <XCircle size={12} />
                  Deny
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
