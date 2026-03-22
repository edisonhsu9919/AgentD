"use client";

import { useState } from "react";
import { useChatStore } from "@/store/chat";
import { ShieldAlert, RefreshCw } from "lucide-react";

interface WaitingRecoveryBannerProps {
  sessionId: string;
}

/**
 * Shown when session status is "waiting" but no pending permissions are in memory.
 * This happens after page refresh or SSE reconnection.
 * Uses the store's fetchPendingPermissions to recover.
 */
export default function WaitingRecoveryBanner({
  sessionId,
}: WaitingRecoveryBannerProps) {
  const [recovering, setRecovering] = useState(false);
  const [failed, setFailed] = useState(false);
  const fetchPendingPermissions = useChatStore((s) => s.fetchPendingPermissions);
  const pendingCount = useChatStore((s) => s.pendingPermissions.length);

  const handleRecover = async () => {
    setRecovering(true);
    setFailed(false);
    try {
      await fetchPendingPermissions(sessionId);
      // If still no permissions after fetch, mark as failed
      if (useChatStore.getState().pendingPermissions.length === 0) {
        setFailed(true);
      }
    } catch {
      setFailed(true);
    } finally {
      setRecovering(false);
    }
  };

  // If permissions were recovered, this banner should disappear
  // (parent checks pendingPermissions.length === 0)
  if (pendingCount > 0) return null;

  return (
    <div className="flow-border border-t border-yellow-500/30 bg-yellow-500/5 px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-yellow-500">
        <ShieldAlert size={16} className="shrink-0" />
        <span className="flex-1">
          Agent is paused — waiting for your approval.
          {failed
            ? " Could not recover pending permissions. The SSE event may have been missed."
            : " Click recover to reload pending approvals."}
        </span>
        <button
          onClick={handleRecover}
          disabled={recovering}
          className="flex shrink-0 items-center gap-1 rounded bg-yellow-500/20 px-2.5 py-1 text-xs text-yellow-500 transition hover:bg-yellow-500/30 disabled:opacity-50"
        >
          <RefreshCw size={12} className={recovering ? "animate-spin" : ""} />
          Recover
        </button>
      </div>
    </div>
  );
}
