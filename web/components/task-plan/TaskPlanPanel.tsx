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
      <div className="pointer-events-none absolute inset-x-0 top-3 z-20 flex justify-center px-5 md:px-6">
        <div className="pointer-events-auto flex w-full max-w-[780px] items-center gap-2 rounded-[22px] bg-bg-primary/96 px-3 py-2.5 shadow-[0_18px_36px_rgba(42,41,51,0.12)] backdrop-blur">
          <CheckCheck size={14} className="text-success" />
          <span className="text-xs font-medium text-text-primary">
            {plan.task.title}
          </span>
          <span className="text-xs text-success">已完成</span>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setExpanded(true)}
              className="flex items-center gap-1 rounded-full px-2 py-1 text-xs text-text-secondary transition hover:bg-white hover:text-text-primary"
            >
              <Eye size={12} />
              查看
            </button>
            <button
              onClick={handleDismiss}
              className="flex items-center gap-1 rounded-full px-2 py-1 text-xs text-text-secondary transition hover:bg-danger/10 hover:text-danger"
            >
              <X size={12} />
              收起
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="pointer-events-none absolute inset-x-0 top-3 z-20 flex justify-center px-5 md:px-6">
      <div className="pointer-events-auto w-full max-w-[780px] rounded-[24px] bg-bg-primary/96 px-4 py-3 shadow-[0_20px_40px_rgba(42,41,51,0.12)] backdrop-blur">
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
              ? "已完成"
              : plan.active
                ? `${completedCount}/${totalCount}`
                : ""}
          </span>
          {allCompleted && (
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setExpanded(false)}
                className="flex items-center gap-1 rounded-full px-2 py-1 text-xs text-text-secondary transition hover:bg-white hover:text-text-primary"
              >
                <EyeOff size={12} />
                收起
              </button>
              <button
                onClick={handleDismiss}
                className="flex items-center gap-1 rounded-full px-2 py-1 text-xs text-text-secondary transition hover:bg-danger/10 hover:text-danger"
              >
                <X size={12} />
                关闭
              </button>
            </div>
          )}
        </div>

        {plan.steps.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {plan.steps.map((step) => (
              <TaskPlanItem key={step.id} step={step} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
