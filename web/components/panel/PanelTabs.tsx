"use client";

import { useLayoutEffect, useRef } from "react";
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
  const tabListRef = useRef<HTMLDivElement | null>(null);
  const sliderRef = useRef<HTMLSpanElement | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  useLayoutEffect(() => {
    const tabListEl = tabListRef.current;
    const sliderEl = sliderRef.current;
    const activeEl = activeType ? tabRefs.current[activeType] : null;

    if (!tabListEl || !sliderEl) return;

    if (!activeEl) {
      sliderEl.style.opacity = "0";
      return;
    }

    const activeRect = activeEl.getBoundingClientRect();
    const slotLeft = activeEl.offsetLeft;
    sliderEl.style.opacity = "1";
    sliderEl.style.width = `${activeRect.width}px`;
    sliderEl.style.transform = `translateX(${slotLeft}px)`;
  }, [activeType, tabs]);

  return (
    <div
      ref={tabListRef}
      className="relative inline-flex max-w-full min-w-0 items-center gap-1.5 overflow-x-auto rounded-full bg-bg-primary px-1.5 py-1.5"
    >
      <span
        ref={sliderRef}
        className="pointer-events-none absolute bottom-1.5 left-0 top-1.5 rounded-full bg-white shadow-[0_10px_22px_rgba(42,41,51,0.07)] transition-[transform,width,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
        style={{ opacity: 0, width: 0 }}
      />
      {tabs.map((tab) => {
        const active = tab.type === activeType;
        return (
          <button
            key={tab.id}
            ref={(node) => {
              tabRefs.current[tab.type] = node;
            }}
            onClick={() => activateType(tab.type)}
            className={`relative z-10 inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-full px-3 text-[11px] leading-none transition ${
              active
                ? "text-text-primary"
                : "text-text-secondary hover:bg-white/70 hover:text-text-primary"
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
