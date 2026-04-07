"use client";

import { FileText, Terminal, Globe } from "lucide-react";
import { usePanelStore } from "@/store/panel";
import type { PanelType } from "@/lib/types";

const tabIcon: Record<PanelType, React.ReactNode> = {
  file_preview: <FileText size={12} />,
  task_output: <Terminal size={12} />,
  html_app: <Globe size={12} />,
};

export default function PanelTabs() {
  const tabs = usePanelStore((s) => s.tabs);
  const activeType = usePanelStore((s) => s.activeType);
  const activateType = usePanelStore((s) => s.activateType);

  return (
    <div className="flex items-center gap-0.5 border-b border-border bg-bg-primary/50 px-1 py-0.5">
      {tabs.map((tab) => {
        const active = tab.id === activeType;
        return (
          <button
            key={tab.id}
            onClick={() => activateType(tab.type)}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] transition ${
              active
                ? "bg-bg-tertiary text-text-primary"
                : "text-text-secondary hover:bg-bg-tertiary/50 hover:text-text-primary"
            } ${tab.attention ? "ring-1 ring-accent/50" : ""}`}
          >
            <span className="shrink-0">
              {tabIcon[tab.type]}
            </span>
            <span>{tab.title}</span>
            {tab.attention && (
              <span className="ml-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            )}
          </button>
        );
      })}
    </div>
  );
}
