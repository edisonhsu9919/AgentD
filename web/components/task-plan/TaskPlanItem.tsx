"use client";

import { Circle, CheckCircle2, Loader2 } from "lucide-react";
import type { TaskPlanStep } from "@/lib/types";

interface TaskPlanItemProps {
  step: TaskPlanStep;
}

const statusConfig = {
  pending: {
    icon: Circle,
    iconClass: "text-text-secondary",
    titleClass: "text-text-secondary",
  },
  in_progress: {
    icon: Loader2,
    iconClass: "animate-spin text-accent",
    titleClass: "text-accent font-medium",
  },
  completed: {
    icon: CheckCircle2,
    iconClass: "text-success",
    titleClass: "text-text-secondary line-through",
  },
};

export default function TaskPlanItem({ step }: TaskPlanItemProps) {
  const config = statusConfig[step.status] || statusConfig.pending;
  const Icon = config.icon;
  const isActive = step.status === "in_progress";

  return (
    <div
      className={`flex gap-2 rounded-[16px] px-3 py-2 ${
        isActive ? "bg-accent/8" : "bg-white/72"
      }`}
    >
      <Icon size={14} className={`mt-0.5 shrink-0 ${config.iconClass}`} />
      <div className="min-w-0 flex-1">
        <span className={`text-[12px] ${config.titleClass}`}>{step.title}</span>
        {isActive && step.detail && (
          <p className="mt-0.5 text-[12px] leading-6 text-text-secondary">
            {step.detail}
          </p>
        )}
      </div>
    </div>
  );
}
