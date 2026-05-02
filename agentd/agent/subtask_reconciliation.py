"""Child-session task reconciliation helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select


@dataclass(frozen=True)
class ReconcileResult:
    reconciled_count: int = 0
    task_ids: list[str] | None = None


async def reconcile_completed_child_tasks(db, parent_session_id: uuid.UUID) -> ReconcileResult:
    """Mark stale child-session tasks completed when the child truth is complete."""
    from agent.run_models import AgentRun
    from agent.task_models import SessionTask
    from agent.tasks import init_task_dir, update_task_status, write_task_result
    from auth.models import User
    from session.models import Message, Session
    from workspace.manager import get_session_dir

    parent = await db.get(Session, parent_session_id)
    if not parent:
        return ReconcileResult()

    user = await db.get(User, parent.user_id)
    parent_session_dir = (
        get_session_dir(user.workspace, str(parent_session_id))
        if user
        else None
    )

    rows = await db.execute(
        select(SessionTask)
        .where(SessionTask.session_id == parent_session_id)
        .where(SessionTask.task_kind == "child_session")
        .where(SessionTask.status.in_(["queued", "running", "waiting"]))
        .where(SessionTask.child_session_id.isnot(None))
    )
    tasks = list(rows.scalars().all())
    reconciled: list[str] = []

    for task in tasks:
        child = await db.get(Session, task.child_session_id)
        if not child or child.status != "idle":
            continue

        latest_run = (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.session_id == task.child_session_id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            not latest_run
            or latest_run.status != "completed"
            or latest_run.error is not None
        ):
            continue

        summary = await _latest_child_summary(db, Message, task.child_session_id)
        task.status = "completed"
        task.result_ref = f".agentd/tasks/{task.id}/result.json"
        reconciled.append(str(task.id))

        if parent_session_dir:
            task_id = str(task.id)
            init_task_dir(parent_session_dir, task_id)
            update_task_status(
                parent_session_dir,
                task_id,
                "completed",
                result_summary=summary[:500],
            )
            write_task_result(parent_session_dir, task_id, {
                "status": "completed",
                "summary": summary,
                "child_session_id": str(task.child_session_id),
                "reconciled": True,
            })

    return ReconcileResult(
        reconciled_count=len(reconciled),
        task_ids=reconciled,
    )


async def _latest_child_summary(db, message_model, child_session_id: uuid.UUID) -> str:
    row = (
        await db.execute(
            select(message_model)
            .where(message_model.session_id == child_session_id)
            .where(message_model.role == "assistant")
            .order_by(message_model.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return ""
    return _parts_to_text(getattr(row, "parts", None) or [])


def _parts_to_text(parts: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("content"):
            chunks.append(str(part.get("content")))
        elif part.get("type") == "summary" and part.get("summary"):
            chunks.append(str(part.get("summary")))
    return "\n".join(chunks).strip()
