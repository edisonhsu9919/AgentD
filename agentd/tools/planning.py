"""planning tool — create or rewrite session task plan (Phase E).

Creates a structured task plan stored at session_dir/.agentd/task_plan.json.
The plan is injected into the system prompt and consumed by the frontend.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from tools.base import BaseTool, ToolContext


class PlanningTool(BaseTool):
    @property
    def name(self) -> str:
        return "planning"

    @property
    def description(self) -> str:
        return (
            "Create or rewrite the task plan for the current session. "
            "Use this at the start of complex, multi-step tasks to define "
            "the overall goal and execution steps. The plan is visible to "
            "the user and guides your subsequent work."
        )

    @property
    def metadata(self) -> "ToolMetadata":
        from tools.base import ToolMetadata
        return ToolMetadata(
            default_permission="allow",
            is_read_only=False,
            is_destructive=False,
            is_concurrency_safe=False,
            can_run_in_background=False,
            result_compressibility="low",
            access_scope="none",
            mutates_session_state=True,
            max_result_size_chars=100_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_title": {
                    "type": "string",
                    "description": "Short title for the overall task.",
                },
                "task_summary": {
                    "type": "string",
                    "description": "Brief summary describing what needs to be accomplished.",
                },
                "steps": {
                    "type": "array",
                    "description": (
                        "Ordered list of execution steps. Each step is an object "
                        "with: id (string, e.g. 's1'), title (string), "
                        "detail (string, optional), status ('pending'|'in_progress'|'completed')."
                    ),
                },
            },
            "required": ["task_title", "steps"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        task_title: str = kwargs["task_title"]
        task_summary: str = kwargs.get("task_summary") or ""
        steps_raw: list = kwargs.get("steps") or []

        if not task_title.strip():
            return {"output": "task_title cannot be empty.", "is_error": True}

        if not steps_raw:
            return {"output": "At least one step is required.", "is_error": True}

        # Validate and normalize steps
        steps = []
        for i, step in enumerate(steps_raw):
            if isinstance(step, dict):
                sid = step.get("id") or f"s{i + 1}"
                title = step.get("title") or f"Step {i + 1}"
                detail = step.get("detail") or ""
                step_status = step.get("status") or "pending"
                if step_status not in ("pending", "in_progress", "completed"):
                    step_status = "pending"
                steps.append({
                    "id": sid,
                    "status": step_status,
                    "title": title,
                    "detail": detail,
                })

        plan = {
            "active": True,
            "task": {
                "title": task_title.strip(),
                "summary": task_summary.strip(),
            },
            "steps": steps,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Write to session_dir/.agentd/task_plan.json
        agentd_dir = os.path.join(ctx.session_dir, ".agentd")
        os.makedirs(agentd_dir, exist_ok=True)
        plan_path = os.path.join(agentd_dir, "task_plan.json")

        try:
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"output": f"Failed to write task plan: {e}", "is_error": True}

        # Publish SSE event if publish is available
        if ctx.publish:
            await ctx.publish(ctx.session_id, {
                "event": "todo_update",
                "plan": plan,
            })

        return {
            "output": _format_plan_output("created", plan),
            "is_error": False,
        }


def _format_plan_output(action: str, plan: dict) -> str:
    """Format full plan state for ToolMessage output.

    Phase L prompt strategy: since Task Plan is no longer injected into the
    system prompt, the model recovers plan state from the most recent
    planning/todo_update ToolMessage in the conversation flow.
    This output must contain the COMPLETE step list with statuses.
    """
    task = plan.get("task", {})
    steps = plan.get("steps", [])
    active = plan.get("active", True)

    parts: list[str] = []
    parts.append(f"## Task Plan ({action})\n")
    if task.get("title"):
        parts.append(f"**Task:** {task['title']}")
    if task.get("summary"):
        parts.append(f"**Summary:** {task['summary']}")
    parts.append(f"**Status:** {'active' if active else 'completed'}\n")

    parts.append("### Steps\n")
    for s in steps:
        icon = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
            s.get("status", "pending"), "[ ]"
        )
        parts.append(f"- {icon} {s.get('title', '???')}")
        if s.get("status") == "in_progress" and s.get("detail"):
            parts.append(f"  Detail: {s['detail']}")

    return "\n".join(parts)
