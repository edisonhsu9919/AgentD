"""todo_update tool — update task plan step statuses (Phase E).

Updates step statuses in the existing task plan at
session_dir/.agentd/task_plan.json. Used during execution to
track progress. When all steps are completed, sets active=false.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

from tools.base import BaseTool, ToolContext


class TodoUpdateTool(BaseTool):
    @property
    def name(self) -> str:
        return "todo_update"

    @property
    def description(self) -> str:
        return (
            "Update step statuses in the current task plan. "
            "Use this to mark steps as completed, in_progress, or pending "
            "as you work through the plan. Can also adjust step titles/details "
            "or set the plan as inactive when done."
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
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": (
                        "Updated step list. Each step: {id, status, title, detail}. "
                        "Provide the FULL step list (not just changed steps) — "
                        "this replaces the existing steps array."
                    ),
                },
                "active": {
                    "type": "boolean",
                    "description": "Set to false when the task is fully completed. Default: true.",
                },
            },
            "required": ["steps"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        steps_raw: list = kwargs.get("steps") or []
        active: bool = kwargs.get("active") if kwargs.get("active") is not None else True

        plan_path = os.path.join(ctx.session_dir, ".agentd", "task_plan.json")

        # Load existing plan
        if not os.path.isfile(plan_path):
            return {
                "output": "No task plan exists. Use the 'planning' tool first to create one.",
                "is_error": True,
            }

        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {"output": f"Failed to read task plan: {e}", "is_error": True}

        # Normalize incoming steps
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

        # Auto-detect completion: if all steps are completed, set active=false
        all_completed = all(s["status"] == "completed" for s in steps) if steps else False
        if all_completed:
            active = False

        plan["steps"] = steps
        plan["active"] = active
        plan["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"output": f"Failed to write task plan: {e}", "is_error": True}

        # Publish SSE event
        if ctx.publish:
            await ctx.publish(ctx.session_id, {
                "event": "todo_update",
                "plan": plan,
            })

        from tools.planning import _format_plan_output
        return {
            "output": _format_plan_output("updated", plan),
            "is_error": False,
        }
