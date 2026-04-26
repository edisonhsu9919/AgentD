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

  return (
    <>
      <div
        className={`fixed inset-0 z-[90] bg-[rgba(42,41,51,0.08)] backdrop-blur-[1px] transition-opacity duration-300 ease-out ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={closePanel}
      />

      <div
        className={`fixed inset-y-3 right-3 z-[100] flex w-[min(46vw,820px)] min-w-[360px] max-w-[820px] transform-gpu flex-col rounded-[18px] bg-white/97 shadow-[0_28px_90px_rgba(42,41,51,0.14)] backdrop-blur transition-transform duration-[460ms] ease-[cubic-bezier(0.2,0.8,0.2,1)] will-change-transform max-md:inset-x-3 max-md:w-auto max-md:min-w-0 ${
          open
            ? "translate-x-0"
            : "pointer-events-none translate-x-[calc(100%+1.25rem)]"
        }`}
      >
        <div className="relative px-3 pb-2 pt-3">
          <div className="flex justify-start">
            <PanelTabs />
          </div>
          <button
            onClick={closePanel}
            className="absolute right-3 top-3 inline-flex h-9 w-9 items-center justify-center rounded-full bg-bg-primary text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
            title="关闭面板"
          >
            <X size={15} />
          </button>
        </div>

        <div className="flex-1 overflow-hidden">
          {activeType ? (
            <PanelRouter sessionId={sessionId} activeType={activeType} />
          ) : (
            <PanelEmptyState />
          )}
        </div>
      </div>
    </>
  );
}

function PanelEmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <PanelRightClose size={32} className="text-text-secondary/30" />
      <div>
        <p className="text-sm font-medium text-text-secondary">
          工作面板
        </p>
        <p className="mt-1 max-w-[240px] text-xs text-text-secondary/60">
          文件预览、任务输出和应用视图都会收束到这里，内容可用时会自动切入对应标签。
        </p>
      </div>
    </div>
  );
}
