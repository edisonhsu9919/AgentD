"use client";

import { useChatStore } from "@/store/chat";

/**
 * Unified session status motion indicator.
 *
 * - running: ECG pulse line + contextual label
 * - queued:  shimmer bar + "Queued" label
 * - waiting: subtle "Paused" label (main motion lives on the permission card)
 * - idle/error: nothing (handled by static badge elsewhere)
 */
export default function SessionStatusIndicator() {
  const status = useChatStore((s) => s.status);
  const streamingToolCalls = useChatStore((s) => s.streamingToolCalls);

  if (status === "running") {
    const hasToolActivity = streamingToolCalls.some(
      (tc) => tc.status === "running",
    );
    const label = hasToolActivity ? "Using tools" : "Thinking";

    return (
      <div className="flex items-center gap-2">
        {/* ECG pulse line */}
        <div className="h-[18px] w-20 overflow-hidden rounded-sm">
          <svg
            className="ecg-line h-full"
            viewBox="0 0 200 18"
            preserveAspectRatio="none"
            style={{ width: "200%" }}
          >
            {/* Two identical paths tiled so the scroll loops seamlessly */}
            <polyline
              points="0,9 15,9 20,9 24,2 28,16 32,9 37,9 100,9 115,9 120,9 124,2 128,16 132,9 137,9 200,9"
              fill="none"
              stroke="var(--color-accent)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.7"
            />
          </svg>
        </div>
        <span className="text-xs text-accent">{label}</span>
      </div>
    );
  }

  if (status === "queued") {
    return (
      <div className="flex items-center gap-2">
        {/* Shimmer bar */}
        <div className="shimmer-bar h-[3px] w-16 rounded-full opacity-60" />
        <span className="text-xs text-yellow-400">Queued</span>
      </div>
    );
  }

  // waiting/idle/error — no motion indicator in header
  return null;
}
