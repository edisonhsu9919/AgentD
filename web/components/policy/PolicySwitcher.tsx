"use client";

import { useState } from "react";
import { useChatStore } from "@/store/chat";
import { ApiRequestError } from "@/lib/api";
import { Shield, ShieldOff, Zap } from "lucide-react";
import type { PolicyMode } from "@/lib/types";

const modes: { value: PolicyMode; label: string; icon: React.ElementType; desc: string }[] = [
  { value: "manual", label: "Manual", icon: Shield, desc: "All tool calls require approval" },
  { value: "autopilot", label: "Autopilot", icon: Zap, desc: "Saved rules auto-approve" },
  { value: "fsd", label: "FSD", icon: ShieldOff, desc: "No HITL, sandbox enforced" },
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
        className="flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs text-text-secondary transition hover:bg-bg-secondary hover:text-text-primary"
        title={currentDef.desc}
      >
        <CurrentIcon size={12} />
        {currentDef.label}
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />

          {/* Dropdown */}
          <div className="absolute right-0 top-full z-50 mt-1 w-52 rounded border border-border bg-bg-secondary shadow-lg">
            {error && (
              <div className="border-b border-border px-3 py-1.5 text-xs text-danger">
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
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition hover:bg-bg-tertiary disabled:opacity-50 ${
                    isActive ? "text-accent" : "text-text-secondary"
                  }`}
                >
                  <Icon size={14} />
                  <div>
                    <div className="font-medium">{m.label}</div>
                    <div className="text-[10px] text-text-secondary">{m.desc}</div>
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
