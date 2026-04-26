"use client";

import { useChatStore } from "@/store/chat";
import AgentDRunningMark from "@/components/brand/AgentDRunningMark";

/**
 * Unified session status motion indicator.
 *
 * - running: ECG pulse line + contextual label
 * - queued: shimmer bar + queue label
 * - waiting: subtle paused state (main motion lives on the permission card)
 * - idle/error: nothing (handled by static badge elsewhere)
 */
export default function SessionStatusIndicator() {
  const status = useChatStore((s) => s.status);

  if (status === "running") {
    return <AgentDRunningMark size={18} />;
  }

  if (status === "queued") {
    return (
      <div className="flex items-center gap-2">
        <div className="shimmer-bar h-[3px] w-16 rounded-full opacity-60" />
        <span className="font-caption text-[11px] tracking-[0.12em] text-warning-foreground">
          排队中
        </span>
      </div>
    );
  }
  return null;
}
