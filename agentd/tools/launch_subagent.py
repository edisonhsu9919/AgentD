"""launch_subagent tool (Phase P3).

Spawns a blocking child task — a child session running a restricted
agent profile. The parent session enters `subtask_waiting` until the
child completes, then receives a summary + artifact path as a
high-level tool result.

The child agent:
- Shares the parent's static prompt layers
- Runs with fsd permission mode (no user interrupts)
- Inherits the parent's working tools except recursive control-plane tools
- Cannot spawn further children or detached processes
"""

import json
import logging
import os
import uuid
from typing import Any

from tools.base import BaseTool, ToolContext, ToolMetadata

logger = logging.getLogger(__name__)


class LaunchSubagentTool(BaseTool):
    @property
    def name(self) -> str:
        return "launch_subagent"

    @property
    def description(self) -> str:
        return (
            "Spawn a child agent to handle a sub-task. The child runs in a "
            "separate session with restricted tools and returns a summary when done. "
            "The current conversation pauses until the child completes. "
            "Use this for focused research, file analysis, or specialized tasks "
            "that benefit from a clean context."
        )

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=False,
            is_concurrency_safe=False,
            can_run_in_background=False,
            result_compressibility="low",
            access_scope="session_only",
            mutates_session_state=True,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Human-readable title for the sub-task.",
                },
                "task_packet": {
                    "type": "string",
                    "description": (
                        "Detailed task description for the child agent. "
                        "Include: goal, scope, expected output format, "
                        "and any relevant file paths or context."
                    ),
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional narrowing list for the child toolset. "
                        "If omitted, the child inherits the parent's tools "
                        "except launch_subagent and launch_detached_process."
                    ),
                },
            },
            "required": ["title", "task_packet"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        title: str = kwargs["title"]
        task_packet: str = kwargs["task_packet"]
        requested_tools: list[str] | None = kwargs.get("allowed_tools")

        # Guard: prevent duplicate child tasks in the same session
        already_waiting = await self._check_already_waiting(ctx.session_id)
        if already_waiting:
            return {
                "output": json.dumps({
                    "status": "rejected",
                    "reason": "This session already has an active child task. "
                              "Wait for it to complete before launching another.",
                }),
                "is_error": True,
            }

        task_id = str(uuid.uuid4())

        resolved_tools = self._resolve_child_tools(requested_tools)
        if requested_tools and not resolved_tools:
            return {
                "output": json.dumps({
                    "status": "rejected",
                    "reason": (
                        "allowed_tools narrowed the child toolset to nothing usable. "
                        "Choose a subset of the parent's tools excluding "
                        "launch_subagent and launch_detached_process."
                    ),
                }),
                "is_error": True,
            }

        # Initialize task filesystem
        from agent.tasks import init_task_dir, write_task_meta
        init_task_dir(ctx.session_dir, task_id)

        # Create child session
        try:
            child_session_id, child_model_id = await self._create_child_session(
                ctx, task_id, title, task_packet,
            )
        except Exception as e:
            logger.error("Failed to create child session: %s", e)
            from agent.tasks import update_task_status
            update_task_status(ctx.session_dir, task_id, "failed", error=str(e))
            return {
                "output": json.dumps({
                    "task_id": task_id,
                    "status": "failed",
                    "error": f"Failed to create child session: {e}",
                }),
                "is_error": True,
            }

        # Write task meta
        write_task_meta(
            ctx.session_dir,
            task_id,
            session_id=ctx.session_id,
            task_kind="child_session",
            blocking_mode="blocking",
            status="running",
            title=title,
            command=task_packet[:500],
            spawned_by_tool=self.name,
            child_session_id=child_session_id,
        )

        # Create DB record
        try:
            await self._create_db_record(
                ctx, task_id, title, task_packet, child_session_id,
            )
        except Exception as e:
            logger.warning("Failed to create session_task DB record: %s", e)

        # Enqueue the child session run
        try:
            run_id = await self._enqueue_child_run(
                ctx,
                child_session_id,
                task_packet,
                requested_tools or [],
                resolved_tools,
                child_model_id,
            )
        except Exception as e:
            logger.error("Failed to enqueue child run: %s", e)
            from agent.tasks import update_task_status
            update_task_status(ctx.session_dir, task_id, "failed", error=str(e))
            return {
                "output": json.dumps({
                    "task_id": task_id,
                    "status": "failed",
                    "error": f"Failed to enqueue child run: {e}",
                }),
                "is_error": True,
            }

        # Update parent session status to subtask_waiting
        try:
            await self._set_parent_waiting(ctx.session_id, task_id)
        except Exception as e:
            logger.warning("Failed to set parent to subtask_waiting: %s", e)

        # Publish event
        if ctx.publish:
            try:
                await ctx.publish(ctx.session_id, {
                    "event": "task_started",
                    "task_id": task_id,
                    "task_kind": "child_session",
                    "child_session_id": child_session_id,
                    "status": "running",
                })
            except Exception:
                pass

        result = {
            "task_id": task_id,
            "status": "waiting_for_child",
            "task_kind": "child_session",
            "blocking_mode": "blocking",
            "child_session_id": child_session_id,
            "run_id": str(run_id),
            "title": title,
            "message": (
                f"Sub-task '{title}' started in child session. "
                f"Waiting for it to complete..."
            ),
            "resolved_tools": resolved_tools,
        }
        return {"output": json.dumps(result, ensure_ascii=False), "is_error": False}

    def _resolve_child_tools(self, requested_tools: list[str] | None) -> list[str]:
        from tools.registry import get_registry

        registry = get_registry()
        requested = {
            name.strip()
            for name in (requested_tools or [])
            if isinstance(name, str) and name.strip()
        }
        resolved = registry.resolve_tool_names(
            tool_profile="child",
            allowed_tools=requested or None,
        )
        return sorted(resolved)

    async def _check_already_waiting(self, session_id: str) -> bool:
        """Check if this session already has a running child task."""
        try:
            from core.database import AsyncSessionLocal
            from session.models import Session
            import session.models  # noqa: F401
            import auth.models  # noqa: F401

            async with AsyncSessionLocal() as db:
                session = await db.get(Session, uuid.UUID(session_id))
                return session is not None and session.status == "subtask_waiting"
        except Exception:
            return False

    async def _create_child_session(
        self,
        ctx: ToolContext,
        task_id: str,
        title: str,
        task_packet: str,
    ) -> tuple[str, str]:
        """Create a child session in DB.

        Returns (child_session_id, model_id).
        """
        from core.database import AsyncSessionLocal
        from model_config.service import resolve_active_model_config
        from session import service as session_svc
        # Ensure all ORM models are registered in SQLAlchemy metadata
        # before creating sessions (users table FK resolution)
        import auth.models  # noqa: F401
        import session.models  # noqa: F401

        async with AsyncSessionLocal() as db:
            # Resolve model_id from current runtime config
            resolved = await resolve_active_model_config(db)
            child = await session_svc.create_session(
                db,
                user_id=uuid.UUID(ctx.user_id),
                model_id=resolved.model_id,
                title=f"[Sub] {title}",
                agent_id="assistant",
                parent_id=uuid.UUID(ctx.session_id),
            )
            await db.commit()
            return str(child.id), resolved.model_id

    async def _create_db_record(
        self,
        ctx: ToolContext,
        task_id: str,
        title: str,
        task_packet: str,
        child_session_id: str,
    ) -> None:
        """Create session_tasks DB record."""
        from core.database import AsyncSessionLocal
        from agent.task_models import SessionTask
        import session.models  # noqa: F401 — ensure FK target is registered

        async with AsyncSessionLocal() as db:
            task = SessionTask(
                id=uuid.UUID(task_id),
                session_id=uuid.UUID(ctx.session_id),
                spawned_by_tool=self.name,
                task_kind="child_session",
                blocking_mode="blocking",
                status="running",
                title=title,
                command=task_packet[:500],
                child_session_id=uuid.UUID(child_session_id),
            )
            db.add(task)
            await db.commit()

    async def _enqueue_child_run(
        self,
        ctx: ToolContext,
        child_session_id: str,
        task_packet: str,
        requested_tools: list[str],
        resolved_tools: list[str],
        model_id: str,
    ) -> uuid.UUID:
        """Enqueue an agent run for the child session.

        The payload must contain all fields the worker expects:
        user_id, user_root, session_dir, agent_id, model_id, user_message.
        Additionally: tool_profile and allowed_tools for child restriction.
        """
        from core.database import AsyncSessionLocal
        from agent.child_session import write_child_session_meta
        from agent.scheduler import enqueue_start
        from permission.policy import SessionPolicy, save_policy
        from session.models import Session
        from workspace.manager import get_session_dir
        from sqlalchemy import update as sa_update

        child_session_dir = get_session_dir(ctx.user_root, child_session_id)
        write_child_session_meta(
            child_session_dir,
            parent_session_id=ctx.session_id,
            parent_session_dir=ctx.session_dir,
            allowed_tools=requested_tools,
            resolved_tools=resolved_tools,
        )
        save_policy(child_session_dir, SessionPolicy(mode="fsd"))

        async with AsyncSessionLocal() as db:
            # Set child session to queued
            await db.execute(
                sa_update(Session)
                .where(Session.id == uuid.UUID(child_session_id))
                .values(status="queued")
            )
            run = await enqueue_start(
                db,
                session_id=uuid.UUID(child_session_id),
                payload={
                    "user_message": task_packet,
                    "user_id": ctx.user_id,
                    "user_root": ctx.user_root,
                    "session_dir": child_session_dir,
                    "agent_id": "assistant",
                    "model_id": model_id,
                    "tool_profile": "child",
                    "allowed_tools": resolved_tools,
                    "parent_session_dir": ctx.session_dir,
                },
            )
            await db.commit()
            return run.id

    async def _set_parent_waiting(self, session_id: str, task_id: str) -> None:
        """Set parent session status to subtask_waiting."""
        from core.database import AsyncSessionLocal
        from session.models import Session
        from sqlalchemy import update as sa_update

        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(Session)
                .where(Session.id == uuid.UUID(session_id))
                .values(status="subtask_waiting")
            )
            await db.commit()
