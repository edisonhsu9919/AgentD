"use client";

import { useState } from "react";
import { useChatStore } from "@/store/chat";
import { Archive, Loader2 } from "lucide-react";

interface CompactBannerProps {
  sessionId: string;
}

export default function CompactBanner({ sessionId }: CompactBannerProps) {
  const [compacting, setCompacting] = useState(false);
  const [error, setError] = useState(false);
  const compactContext = useChatStore((s) => s.compactContext);

  const handleCompact = async () => {
    setCompacting(true);
    setError(false);
    try {
      await compactContext(sessionId);
    } catch {
      setError(true);
    } finally {
      setCompacting(false);
    }
  };

  return (
    <div className="border-t border-yellow-500/30 bg-yellow-500/5 px-4 py-2.5">
      <div className="flex items-center gap-2 text-sm text-yellow-500">
        <Archive size={14} className="shrink-0" />
        <span className="flex-1 text-xs">
          {error
            ? "Compaction failed — try again or wait for automatic compaction."
            : "Context usage is high. Compact to free up space and keep the session running smoothly."}
        </span>
        <button
          onClick={handleCompact}
          disabled={compacting}
          className="flex shrink-0 items-center gap-1 rounded bg-yellow-500/20 px-2.5 py-1 text-xs text-yellow-500 transition hover:bg-yellow-500/30 disabled:opacity-50"
        >
          {compacting ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Archive size={12} />
          )}
          {compacting ? "Compacting..." : "Compact Context"}
        </button>
      </div>
    </div>
  );
}
