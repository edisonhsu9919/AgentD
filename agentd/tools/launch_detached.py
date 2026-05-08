"""launch_detached_process tool (Phase P3).

Explicitly starts a long-running script/command as a detached background
process. The current agent run returns immediately after launch — the
worker and LLM are released. The process runs independently, with
stdout/stderr streamed to .agentd/tasks/{task_id}/.

This is NOT the same as bash — bash is foreground and blocks the run.
This tool is for tasks the user wants to fire-and-forget.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from agent.runtime_env import resolve_command_execution
from tools.base import BaseTool, ToolContext, ToolMetadata
from workspace.manager import is_internal_path, validate_path

logger = logging.getLogger(__name__)

# Maximum concurrent detached processes per session
_MAX_CONCURRENT_TASKS = 5

# Maximum runtime for detached processes (12 hours)
_MAX_RUNTIME_SECONDS = 12 * 60 * 60


class LaunchDetachedProcessTool(BaseTool):
    @property
    def name(self) -> str:
        return "launch_detached_process"

    @property
    def description(self) -> str:
        return (
            "Launch a long-running script or command as a background process. "
            "The current conversation continues immediately — the process runs "
            "independently. Use this for batch processing, data generation, "
            "or any task that takes a long time. "
            "The user can check progress via the Task Output panel."
        )

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=False,
            is_concurrency_safe=False,
            can_run_in_background=True,
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
                    "description": "Human-readable title for the task.",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to run in the background.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (relative to session). Defaults to session root.",
                },
            },
            "required": ["title", "command"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        title: str = kwargs["title"]
        command: str = kwargs["command"]
        cwd: str = kwargs.get("cwd", "")

        from core.cli_registry import registry
        from tools.bash import _has_outside_paths, _is_blacklisted

        resolved_cmd, svc = registry.resolve_command(command)

        # Blacklist check
        if _is_blacklisted(resolved_cmd):
            return {"output": "permission_denied: command matches blacklist", "is_error": True}

        # Workspace path restriction (§7.3)
        if svc:
            if not svc.supports_detached:
                return {"output": f"permission_denied: CLI service '{svc.name}' does not support detached mode", "is_error": True}
            import shlex
            try:
                parts = shlex.split(command)
                args_str = command[command.find(parts[0]) + len(parts[0]):]
            except Exception:
                args_str = command
            if _has_outside_paths(args_str, ctx.workspace_dir):
                return {"output": "permission_denied: service arguments reference paths outside workspace", "is_error": True}
        else:
            if _has_outside_paths(resolved_cmd, ctx.workspace_dir):
                return {"output": "permission_denied: command references paths outside workspace", "is_error": True}

        # Resolve working directory
        if cwd:
            if is_internal_path(cwd):
                return {"output": "Access denied: path points to internal system directory", "is_error": True}
            try:
                abs_cwd = validate_path(ctx.workspace_dir, cwd)
            except PermissionError as e:
                return {"output": str(e), "is_error": True}
            if not os.path.isdir(abs_cwd):
                return {"output": f"Working directory not found: {cwd}", "is_error": True}
        else:
            if svc and svc.cwd_policy == "entrypoint_dir":
                abs_cwd = os.path.dirname(svc.entrypoint)
            else:
                abs_cwd = ctx.workspace_dir

        execution = resolve_command_execution(
            ctx,
            resolved_cmd,
            service=svc,
            workdir=abs_cwd,
        )

        # Generate task ID
        task_id = str(uuid.uuid4())

        # Initialize task filesystem structure
        from agent.tasks import init_task_dir, write_task_meta
        task_dir = init_task_dir(ctx.session_dir, task_id)
        meta = write_task_meta(
            ctx.session_dir,
            task_id,
            session_id=ctx.session_id,
            task_kind="process",
            blocking_mode="detached",
            status="running",
            title=title,
            command=command,
            spawned_by_tool=self.name,
            extra=execution.as_metadata(),
        )

        # Create DB record
        try:
            await self._create_db_record(ctx, task_id, title, command, meta)
        except Exception as e:
            logger.warning("Failed to create session_task DB record: %s", e)
            # Continue anyway — filesystem is the primary truth for task output

        # Launch subprocess
        stdout_path = os.path.join(task_dir, "stdout.log")
        stderr_path = os.path.join(task_dir, "stderr.log")

        try:
            # Pass task metadata as env vars so the subprocess can find its own task dir
            env = os.environ.copy()
            env["AGENTD_TASK_ID"] = task_id
            env["AGENTD_TASK_DIR"] = task_dir
            env["AGENTD_SESSION_DIR"] = ctx.session_dir
            env["AGENTD_SESSION_ID"] = ctx.session_id
            env["AGENTD_USER_ID"] = ctx.user_id
            env = execution.build_process_env(env)

            process = await asyncio.create_subprocess_shell(
                resolved_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=execution.workdir,
                env=env,
            )
        except Exception as e:
            from agent.tasks import update_task_status
            update_task_status(ctx.session_dir, task_id, "failed", error=str(e))
            return {
                "output": json.dumps({
                    "task_id": task_id,
                    "status": "failed",
                    "error": f"Failed to start process: {e}",
                }),
                "is_error": True,
            }

        # Update meta with PID
        from agent.tasks import update_task_status
        update_task_status(ctx.session_dir, task_id, "running", pid=process.pid)

        # Sync PID to DB
        await _update_db_pid(task_id, process.pid)

        # Spawn background monitor (runs on the worker's event loop)
        asyncio.create_task(
            _monitor_process(
                process=process,
                session_id=ctx.session_id,
                session_dir=ctx.session_dir,
                task_id=task_id,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                publish=ctx.publish,
            )
        )

        # Return immediately — process runs in background
        result = {
            "task_id": task_id,
            "status": "launched",
            "task_kind": "process",
            "blocking_mode": "detached",
            "title": title,
            "pid": process.pid,
            "artifact_root": meta["artifact_root"],
            "message": f"Background task '{title}' started (PID {process.pid}). "
                       f"Check Task Output panel for progress.",
        }
        return {"output": json.dumps(result, ensure_ascii=False), "is_error": False}

    async def _create_db_record(
        self, ctx: ToolContext, task_id: str, title: str, command: str, meta: dict
    ) -> None:
        """Create a session_tasks DB record for indexing."""
        from core.database import AsyncSessionLocal
        from agent.task_models import SessionTask
        import session.models  # noqa: F401 — ensure FK target is registered

        async with AsyncSessionLocal() as db:
            task = SessionTask(
                id=uuid.UUID(task_id),
                session_id=uuid.UUID(ctx.session_id),
                spawned_by_tool=self.name,
                task_kind="process",
                blocking_mode="detached",
                status="running",
                title=title,
                command=command,
                stdout_path=meta["stdout_path"],
                stderr_path=meta["stderr_path"],
                artifact_root=meta["artifact_root"],
            )
            db.add(task)
            await db.commit()


async def _monitor_process(
    *,
    process: asyncio.subprocess.Process,
    session_id: str,
    session_dir: str,
    task_id: str,
    stdout_path: str,
    stderr_path: str,
    publish,
) -> None:
    """Background task that monitors a detached process.

    Streams stdout/stderr to log files, updates status on completion,
    and publishes SSE events for the frontend Task Output panel.
    """
    from agent.tasks import update_task_status, write_task_result

    # Publish task_started event
    if publish:
        try:
            await publish(session_id, {
                "event": "task_started",
                "task_id": task_id,
                "status": "running",
            })
        except Exception:
            pass

    streams_done = asyncio.Event()

    # Stream stdout and stderr concurrently
    async def _stream_to_file(stream, filepath):
        with open(filepath, "ab") as f:
            while True:
                line = await stream.readline()
                if not line:
                    break
                f.write(line)
                f.flush()

    # Phase P6-E: monitor for panel_content.json and push via SSE
    async def _monitor_panel_content():
        """Watch for panel_content.json and push html_app content to frontend."""
        import json as _json
        task_dir = os.path.dirname(stdout_path)
        panel_path = os.path.join(task_dir, "panel_content.json")
        pushed = False
        while not pushed and not streams_done.is_set():
            await asyncio.sleep(2)
            if os.path.isfile(panel_path):
                try:
                    with open(panel_path, "r", encoding="utf-8") as f:
                        panel_data = _json.load(f)
                    if publish:
                        await publish(session_id, {
                            "event": "panel_update",
                            "panel_type": "html_app",
                            "panel_content": panel_data,
                        })
                        logger.info("Pushed panel_content for task %s", task_id)
                    pushed = True
                except Exception as e:
                    logger.warning("Failed to push panel_content: %s", e)
                    pushed = True  # Don't retry endlessly
        
        # Final check in case it was written right before streams finished
        if not pushed and os.path.isfile(panel_path):
            try:
                with open(panel_path, "r", encoding="utf-8") as f:
                    panel_data = _json.load(f)
                if publish:
                    await publish(session_id, {
                        "event": "panel_update",
                        "panel_type": "html_app",
                        "panel_content": panel_data,
                    })
                    logger.info("Pushed panel_content for task %s on final check", task_id)
            except Exception:
                pass

    async def _run_streams():
        """Run streams and signal completion."""
        await asyncio.gather(
            _stream_to_file(process.stdout, stdout_path),
            _stream_to_file(process.stderr, stderr_path)
        )
        streams_done.set()

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _run_streams(),
                _monitor_panel_content(),
            ),
            timeout=_MAX_RUNTIME_SECONDS,
        )
        returncode = await process.wait()
    except asyncio.TimeoutError:
        # TTL exceeded — kill the process
        logger.warning("Detached task %s exceeded %ds TTL, terminating", task_id, _MAX_RUNTIME_SECONDS)
        timed_out = True
        try:
            process.terminate()
            await asyncio.sleep(2)
            if process.returncode is None:
                process.kill()
        except Exception:
            pass
        returncode = process.returncode or -15
    except Exception as e:
        logger.error("Process monitor error for task %s: %s", task_id, e)
        update_task_status(session_dir, task_id, "failed", error=str(e))
        write_task_result(session_dir, task_id, {
            "status": "failed",
            "error": str(e),
            "source": "monitor_exception",
        })
        _update_db_status(session_id, task_id, "failed", str(e))
        if publish:
            try:
                await publish(session_id, {
                    "event": "task_failed",
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(e),
                    "source": "monitor_exception",
                })
            except Exception:
                pass
        return

    # Determine final status
    if timed_out:
        final_status = "timed_out"
        error = f"Process exceeded maximum runtime of {_MAX_RUNTIME_SECONDS}s"
    elif returncode == 0:
        final_status = "completed"
        error = None
    else:
        final_status = "failed"
        error = f"Process exited with code {returncode}"

    update_task_status(session_dir, task_id, final_status, error=error)
    write_task_result(session_dir, task_id, {
        "returncode": returncode,
        "status": final_status,
    })

    # Update DB
    _update_db_status(session_id, task_id, final_status, error)

    # Publish completion event
    if publish:
        try:
            await publish(session_id, {
                "event": "task_completed" if returncode == 0 else "task_failed",
                "task_id": task_id,
                "status": final_status,
                "returncode": returncode,
            })
        except Exception:
            pass

    logger.info(
        "Detached task %s finished: status=%s returncode=%d",
        task_id, final_status, returncode,
    )


async def _update_db_pid(task_id: str, pid: int) -> None:
    """Best-effort DB PID sync after subprocess launch."""
    try:
        from core.database import AsyncSessionLocal
        from sqlalchemy import update as sa_update
        from agent.task_models import SessionTask
        import session.models  # noqa: F401

        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(SessionTask)
                .where(SessionTask.id == uuid.UUID(task_id))
                .values(pid=pid)
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to sync task PID to DB: %s", e)


def _update_db_status(session_id: str, task_id: str, status: str, error: str | None) -> None:
    """Best-effort DB status update (sync wrapper for fire-and-forget)."""
    import asyncio as _asyncio

    async def _do():
        try:
            from core.database import AsyncSessionLocal
            from sqlalchemy import update as sa_update
            from agent.task_models import SessionTask
            import session.models  # noqa: F401

            async with AsyncSessionLocal() as db:
                await db.execute(
                    sa_update(SessionTask)
                    .where(SessionTask.id == uuid.UUID(task_id))
                    .values(status=status, error=error)
                )
                await db.commit()
        except Exception as e:
            logger.warning("Failed to update task DB status: %s", e)

    try:
        loop = _asyncio.get_running_loop()
        loop.create_task(_do())
    except RuntimeError:
        pass
