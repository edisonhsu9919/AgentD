"""Session task management (Phase P3).

Manages long-running task instances — both detached process jobs and
blocking child tasks (subagent). Provides:

- File-system persistence for task output (.agentd/tasks/{task_id}/)
- Helpers for creating, updating, and querying task state
- meta.json schema definition

The DB layer (session_tasks table) provides the lightweight index;
this module manages the heavy file-system content.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ── Task output file structure ──────────────────────────────────────────────

TASKS_DIR = ".agentd/tasks"


def get_task_dir(session_dir: str, task_id: str) -> str:
    """Return the absolute path for a task's output directory."""
    return os.path.join(session_dir, TASKS_DIR, task_id)


def init_task_dir(session_dir: str, task_id: str) -> str:
    """Create the task output directory structure.

    Creates:
        .agentd/tasks/{task_id}/
            meta.json
            stdout.log  (empty)
            stderr.log  (empty)
            artifacts/

    Returns the absolute task directory path.
    """
    task_dir = get_task_dir(session_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(os.path.join(task_dir, "artifacts"), exist_ok=True)

    # Create empty log files
    for fname in ("stdout.log", "stderr.log"):
        path = os.path.join(task_dir, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                pass

    return task_dir


def write_task_meta(
    session_dir: str,
    task_id: str,
    *,
    session_id: str,
    task_kind: Literal["process", "child_session"],
    blocking_mode: Literal["detached", "blocking"],
    status: str = "queued",
    title: str = "",
    command: str = "",
    spawned_by_tool: str = "",
    tool_call_id: str = "",
    child_session_id: str | None = None,
    pid: int | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Write or update meta.json for a task.

    Returns the full meta dict.
    """
    task_dir = get_task_dir(session_dir, task_id)
    now = datetime.now(timezone.utc).isoformat()

    meta: dict[str, Any] = {
        "task_id": task_id,
        "session_id": session_id,
        "task_kind": task_kind,
        "blocking_mode": blocking_mode,
        "status": status,
        "title": title,
        "command": command,
        "spawned_by_tool": spawned_by_tool,
        "tool_call_id": tool_call_id,
        "child_session_id": child_session_id,
        "pid": pid,
        "artifact_root": os.path.join(task_dir, "artifacts"),
        "stdout_path": os.path.join(task_dir, "stdout.log"),
        "stderr_path": os.path.join(task_dir, "stderr.log"),
        "created_at": now,
        "updated_at": now,
    }
    if extra:
        meta.update(extra)

    meta_path = os.path.join(task_dir, "meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return meta


def update_task_status(
    session_dir: str,
    task_id: str,
    status: str,
    *,
    pid: int | None = None,
    error: str | None = None,
    result_summary: str | None = None,
) -> dict[str, Any] | None:
    """Update the status in meta.json. Returns updated meta or None."""
    task_dir = get_task_dir(session_dir, task_id)
    meta_path = os.path.join(task_dir, "meta.json")

    if not os.path.isfile(meta_path):
        return None

    with open(meta_path, "r") as f:
        meta = json.load(f)

    meta["status"] = status
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    if pid is not None:
        meta["pid"] = pid
    if error is not None:
        meta["error"] = error
    if result_summary is not None:
        meta["result_summary"] = result_summary

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return meta


def read_task_meta(session_dir: str, task_id: str) -> dict[str, Any] | None:
    """Read meta.json for a task. Returns None if not found."""
    meta_path = os.path.join(get_task_dir(session_dir, task_id), "meta.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, "r") as f:
        return json.load(f)


def list_tasks(session_dir: str) -> list[dict[str, Any]]:
    """List all tasks in the session by scanning .agentd/tasks/."""
    tasks_root = os.path.join(session_dir, TASKS_DIR)
    if not os.path.isdir(tasks_root):
        return []

    result = []
    for name in os.listdir(tasks_root):
        meta = read_task_meta(session_dir, name)
        if meta:
            result.append(meta)

    # Sort by created_at descending (most recent first)
    result.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return result


def write_task_result(
    session_dir: str,
    task_id: str,
    result: dict[str, Any],
) -> None:
    """Write result.json for a completed task."""
    task_dir = get_task_dir(session_dir, task_id)
    result_path = os.path.join(task_dir, "result.json")
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def read_task_stdout(
    session_dir: str,
    task_id: str,
    tail_lines: int = 100,
) -> str:
    """Read the last N lines of stdout.log."""
    path = os.path.join(get_task_dir(session_dir, task_id), "stdout.log")
    if not os.path.isfile(path):
        return ""
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-tail_lines:])


def read_task_stderr(
    session_dir: str,
    task_id: str,
    tail_lines: int = 100,
) -> str:
    """Read the last N lines of stderr.log."""
    path = os.path.join(get_task_dir(session_dir, task_id), "stderr.log")
    if not os.path.isfile(path):
        return ""
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-tail_lines:])
