"""Agent runner — thin compatibility shim (Phase C).

Delegates all execution to ``agent.executor``. Kept for backward
compatibility with existing ``session/router.py`` imports during Phase C
transition. The ``start_loop`` / ``resume_loop`` / ``abort_loop`` APIs
are deprecated — routers should enqueue via ``agent.scheduler`` and
let workers execute via ``agent.executor``.

Post-Phase C cleanup: remove this file once all callers use scheduler.
"""

import asyncio
import traceback
import uuid

from agent.executor import execute_start, execute_resume
from core.config import settings
from core.events import event_bus


# ── Deprecated in-process tracking (kept for single-process fallback) ────

_running_tasks: dict[str, asyncio.Task] = {}
_pending_permissions: dict[str, list[str]] = {}


def get_pending_permissions(session_id: str) -> list[str]:
    """Return the list of pending permission IDs for a session.

    DEPRECATED: Phase C stores pending permissions in DB only.
    Kept for backward compatibility with permission/router.py.
    """
    return list(_pending_permissions.get(session_id, []))


# ── Legacy start/resume/abort (delegate to executor) ────────────────────


async def start_loop(
    session_id: str,
    user_id: str,
    user_root: str,
    session_dir: str,
    agent_id: str,
    model_id: str,
    user_message: str,
) -> None:
    """Launch the agent loop as a background task.

    DEPRECATED: Use ``scheduler.enqueue_start()`` + worker instead.
    """
    task = asyncio.create_task(
        _run_start(session_id, user_id, user_root, session_dir, agent_id, model_id, user_message)
    )
    _running_tasks[session_id] = task

    def _on_done(t: asyncio.Task):
        _running_tasks.pop(session_id, None)

    task.add_done_callback(_on_done)


async def _run_start(
    session_id: str,
    user_id: str,
    user_root: str,
    session_dir: str,
    agent_id: str,
    model_id: str,
    user_message: str,
) -> None:
    """Wrapper that delegates to executor with event_bus.publish."""
    await event_bus.publish(session_id, {"event": "status_change", "status": "running"})

    try:
        await execute_start(
            session_id=session_id,
            user_id=user_id,
            user_root=user_root,
            session_dir=session_dir,
            agent_id=agent_id,
            model_id=model_id,
            user_message=user_message,
            publish=event_bus.publish,
        )
    except asyncio.CancelledError:
        from agent.executor import _update_db_status
        await _update_db_status(session_id, "idle")
        await event_bus.publish(session_id, {"event": "status_change", "status": "idle"})
    except Exception as e:
        from agent.executor import _update_db_status
        await _update_db_status(session_id, "error")
        await event_bus.publish(session_id, {"event": "status_change", "status": "error"})
        await event_bus.publish(session_id, {
            "event": "error", "code": "llm_error", "message": str(e),
        })
        if settings.debug:
            traceback.print_exc()


async def resume_loop(session_id: str, decisions: list[dict]) -> None:
    """Resume an interrupted agent loop with batch decisions.

    DEPRECATED: Use ``scheduler.enqueue_resume()`` + worker instead.
    """
    _pending_permissions.pop(session_id, None)

    task = asyncio.create_task(_run_resume(session_id, decisions))
    _running_tasks[session_id] = task

    def _on_done(t: asyncio.Task):
        _running_tasks.pop(session_id, None)

    task.add_done_callback(_on_done)


async def _run_resume(session_id: str, decisions: list[dict]) -> None:
    """Wrapper that delegates resume to executor."""
    from agent.executor import _update_db_status

    await _update_db_status(session_id, "running")
    await event_bus.publish(session_id, {"event": "status_change", "status": "running"})

    try:
        await execute_resume(
            session_id=session_id,
            decisions=decisions,
            publish=event_bus.publish,
        )
    except asyncio.CancelledError:
        await _update_db_status(session_id, "idle")
        await event_bus.publish(session_id, {"event": "status_change", "status": "idle"})
    except Exception as e:
        await _update_db_status(session_id, "error")
        await event_bus.publish(session_id, {"event": "status_change", "status": "error"})
        await event_bus.publish(session_id, {
            "event": "error", "code": "llm_error", "message": str(e),
        })
        if settings.debug:
            traceback.print_exc()


async def abort_loop(session_id: str) -> bool:
    """Cancel a running agent loop. Returns True if a task was cancelled.

    DEPRECATED: Use ``scheduler.enqueue_abort()`` + worker instead.
    """
    task = _running_tasks.get(session_id)
    if task and not task.done():
        task.cancel()
        return True
    return False
