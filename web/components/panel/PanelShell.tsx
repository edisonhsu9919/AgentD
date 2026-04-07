"use client";

import { X, PanelRightClose } from "lucide-react";
import { usePanelStore } from "@/store/panel";
import PanelTabs from "./PanelTabs";
import PanelRouter from "./PanelRouter";

interface PanelShellProps {
  sessionId: string;
}

export default function PanelShell({ sessionId }: PanelShellProps) {
  const open = usePanelStore((s) => s.open);
  const closePanel = usePanelStore((s) => s.closePanel);
  const activeType = usePanelStore((s) => s.activeType);
  const tabs = usePanelStore((s) => s.tabs);
  const filePreviewPath = usePanelStore((s) => s.filePreviewPath);
  const knowledgeDocTitle = usePanelStore((s) => s.knowledgeDocTitle);
  const fileInspect = usePanelStore((s) => s.fileInspect);

  if (!open) return null;

  const isKnowledge = filePreviewPath?.startsWith("knowledge:");

  // Build display title from active type
  const activeTab = tabs.find((t) => t.id === activeType);
  const displayTitle =
    activeType === "file_preview" && filePreviewPath
      ? isKnowledge
        ? knowledgeDocTitle || "Knowledge Document"
        : filePreviewPath.split("/").pop() || "File Preview"
      : activeTab?.title || "Work Panel";
  const displaySubtitle =
    activeType === "file_preview" && filePreviewPath
      ? isKnowledge
        ? fileInspect?.path || undefined
        : filePreviewPath
      : undefined;

  return (
    <div className="fixed inset-y-0 right-0 z-40 flex w-[50vw] min-w-[400px] max-w-[800px] flex-col border-l border-border bg-bg-secondary shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {activeType ? (
            <div className="min-w-0">
              <div className="truncate text-xs font-medium text-text-primary">
                {displayTitle}
              </div>
              {displaySubtitle && (
                <div className="truncate text-[10px] text-text-secondary">
                  {displaySubtitle}
                </div>
              )}
            </div>
          ) : (
            <span className="text-xs font-medium text-text-secondary">
              Work Panel
            </span>
          )}
        </div>

        <button
          onClick={closePanel}
          className="shrink-0 rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50 hover:text-text-primary"
          title="Close panel"
        >
          <X size={14} />
        </button>
      </div>

      {/* Tabs strip */}
      <PanelTabs />

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {activeType ? (
          <PanelRouter sessionId={sessionId} activeType={activeType} />
        ) : (
          <PanelEmptyState />
        )}
      </div>
    </div>
  );
}

function PanelEmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <PanelRightClose size={32} className="text-text-secondary/30" />
      <div>
        <p className="text-sm font-medium text-text-secondary">
          Work Panel
        </p>
        <p className="mt-1 max-w-[240px] text-xs text-text-secondary/60">
          Click a file to preview, or view task output here. Panel content
          will appear when available.
        </p>
      </div>
    </div>
  );
}
