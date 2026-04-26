"use client";

import { useState } from "react";
import { useChatStore } from "@/store/chat";
import { ApiRequestError } from "@/lib/api";
import { Shield, ShieldOff, Zap } from "lucide-react";
import type { PolicyMode } from "@/lib/types";

const modes: { value: PolicyMode; label: string; icon: React.ElementType; desc: string }[] = [
  { value: "manual", label: "manual", icon: Shield, desc: "所有工具调用都需要人工批准" },
  { value: "autopilot", label: "autopilot", icon: Zap, desc: "已保存规则的调用会自动批准" },
  { value: "fsd", label: "FSD", icon: ShieldOff, desc: "不走人工审批，依赖沙箱边界执行" },
];

interface PolicySwitcherProps {
  sessionId: string;
}

export default function PolicySwitcher({ sessionId }: PolicySwitcherProps) {
  const policy = useChatStore((s) => s.policy);
  const updatePolicy = useChatStore((s) => s.updatePolicy);
  const [open, setOpen] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentMode = policy?.mode || "manual";
  const currentDef = modes.find((m) => m.value === currentMode) || modes[0];
  const CurrentIcon = currentDef.icon;

  const handleSwitch = async (mode: PolicyMode) => {
    if (mode === currentMode) {
      setOpen(false);
      return;
    }
    setUpdating(true);
    setError(null);
    try {
      await updatePolicy(sessionId, mode);
      setOpen(false);
    } catch (err) {
      const msg = err instanceof ApiRequestError ? err.message : "Failed to update policy";
      setError(msg);
    } finally {
      setUpdating(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex h-11 w-11 items-center justify-center rounded-full text-text-secondary transition hover:bg-white hover:text-text-primary"
        title={currentDef.desc}
      >
        <CurrentIcon size={15} />
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />

          {/* Dropdown */}
          <div className="absolute bottom-[calc(100%+0.65rem)] left-0 z-50 w-60 rounded-[18px] border border-border bg-white/96 p-1.5 shadow-[0_18px_50px_rgba(42,41,51,0.12)] backdrop-blur">
            {error && (
              <div className="mb-1 rounded-[14px] bg-danger/10 px-3 py-2 text-xs text-danger">
                {error}
              </div>
            )}
            {modes.map((m) => {
              const Icon = m.icon;
              const isActive = m.value === currentMode;
              return (
                <button
                  key={m.value}
                  onClick={() => handleSwitch(m.value)}
                  disabled={updating}
                  className={`flex w-full items-center gap-3 rounded-[14px] px-3 py-2.5 text-left text-xs transition hover:bg-bg-primary disabled:opacity-50 ${
                    isActive ? "bg-accent/8 text-accent" : "text-text-secondary"
                  }`}
                >
                  <Icon size={13} />
                  <div>
                    <div className="text-[13px] font-medium">{m.label}</div>
                    <div className="mt-0.5 text-[10px] leading-4 text-text-secondary">{m.desc}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
