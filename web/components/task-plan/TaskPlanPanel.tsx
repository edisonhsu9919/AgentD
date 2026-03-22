"use client";

import { useState } from "react";
import { useTaskPlanStore } from "@/store/taskPlan";
import { useSessionStore } from "@/store/session";
import TaskPlanItem from "./TaskPlanItem";
import { ListChecks, CheckCheck, Eye, EyeOff, X } from "lucide-react";

export default function TaskPlanPanel() {
  const plan = useTaskPlanStore((s) => s.plan);
  const dismissTaskPlan = useTaskPlanStore((s) => s.dismissTaskPlan);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);

  const [expanded, setExpanded] = useState(false);

  // No plan or empty — render nothing
  if (!plan.task.title && plan.steps.length === 0) {
    return null;
  }

  const allCompleted =
    !plan.active &&
    plan.steps.length > 0 &&
    plan.steps.every((s) => s.status === "completed");

  const completedCount = plan.steps.filter(
    (s) => s.status === "completed",
  ).length;
  const totalCount = plan.steps.length;

  const handleDismiss = () => {
    if (currentSessionId) {
      dismissTaskPlan(currentSessionId);
    }
  };

  // Completed state: collapsed summary bar
  if (allCompleted && !expanded) {
    return (
      <div className="flex items-center gap-2 border-b border-border bg-bg-secondary/50 px-4 py-2">
        <CheckCheck size={14} className="text-success" />
        <span className="text-xs font-medium text-text-primary">
          {plan.task.title}
        </span>
        <span className="text-xs text-success">Completed</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setExpanded(true)}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
          >
            <Eye size={12} />
            View
          </button>
          <button
            onClick={handleDismiss}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-secondary transition hover:bg-danger/10 hover:text-danger"
          >
            <X size={12} />
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="border-b border-border bg-bg-secondary/50 px-4 py-2">
      {/* Header */}
      <div className="flex items-center gap-2">
        {allCompleted ? (
          <CheckCheck size={14} className="text-success" />
        ) : (
          <ListChecks size={14} className="text-accent" />
        )}
        <span className="text-xs font-medium text-text-primary">
          {plan.task.title}
        </span>
        <span className="text-xs text-text-secondary">
          {allCompleted
            ? "Completed"
            : plan.active
              ? `${completedCount}/${totalCount}`
              : ""}
        </span>
        {/* Collapse / Dismiss controls for expanded completed view */}
        {allCompleted && (
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setExpanded(false)}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
            >
              <EyeOff size={12} />
              Collapse
            </button>
            <button
              onClick={handleDismiss}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-secondary transition hover:bg-danger/10 hover:text-danger"
            >
              <X size={12} />
              Dismiss
            </button>
          </div>
        )}
      </div>

      {/* Steps */}
      {plan.steps.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {plan.steps.map((step) => (
            <TaskPlanItem key={step.id} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}
