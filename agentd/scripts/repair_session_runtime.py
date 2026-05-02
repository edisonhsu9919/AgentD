#!/usr/bin/env python3
"""Repair narrow runtime transcript damage for a single session.

This script handles the v0.4.4 incident shape where a compaction
``[Context Summary]`` user message was inserted between an assistant tool-call
group and its tool results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update

from agent.run_models import AgentRun
from agent.projection_consistency import (
    inspect_session_projection_consistency,
    mark_latest_failed_run_projection_recoverable,
    repair_db_projection_ahead,
)
from agent.runtime_integrity import inspect_db_transcript_tail
from agent.subtask_bridge import (
    _enqueue_parent_continuation,
    _parent_has_queued_or_active_run,
    _parent_session_dir,
    bridge_reconcilable_child_tasks,
    inspect_parent_checkpoint_for_subtask_continuation,
    repair_parent_checkpoint_before_subtask_continuation,
)
from agent.task_models import SessionTask
from auth.models import User
from core.database import AsyncSessionLocal
from session.models import Message, Session


@dataclass(frozen=True)
class SummaryMove:
    summary_id: uuid.UUID
    summary_seq: int
    target_seq: int
    assistant_seq: int
    required_tool_call_ids: list[str]


def _tool_call_ids(parts: list[dict[str, Any]]) -> list[str]:
    return [
        str(part.get("tool_call_id"))
        for part in parts or []
        if isinstance(part, dict)
        and part.get("type") == "tool_call"
        and part.get("tool_call_id")
    ]


def _tool_result_ids(parts: list[dict[str, Any]]) -> list[str]:
    return [
        str(part.get("tool_call_id"))
        for part in parts or []
        if isinstance(part, dict)
        and part.get("type") == "tool_result"
        and part.get("tool_call_id")
    ]


def _is_context_summary(message: Message) -> bool:
    if getattr(message, "is_summary", False):
        return True
    for part in getattr(message, "parts", None) or []:
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and str(part.get("content") or "").startswith("[Context Summary]")
        ):
            return True
    return False


def plan_summary_moves(messages: list[Message]) -> list[SummaryMove]:
    ordered = sorted(messages, key=lambda msg: msg.seq)
    moves: list[SummaryMove] = []

    for idx, message in enumerate(ordered):
        if message.role != "assistant":
            continue
        required_ids = _tool_call_ids(message.parts or [])
        if not required_ids:
            continue

        found: dict[str, int] = {}
        summaries: list[Message] = []
        j = idx + 1
        while j < len(ordered):
            current = ordered[j]
            if current.role == "assistant":
                break
            if current.role == "tool":
                for tool_id in _tool_result_ids(current.parts or []):
                    if tool_id in required_ids:
                        found[tool_id] = current.seq
            elif current.role == "user" and _is_context_summary(current):
                summaries.append(current)
            elif current.role != "tool":
                break

            if all(tool_id in found for tool_id in required_ids):
                break
            j += 1

        if not all(tool_id in found for tool_id in required_ids):
            continue
        target_seq = max(found.values())
        for summary in summaries:
            if message.seq < summary.seq < target_seq:
                moves.append(SummaryMove(
                    summary_id=summary.id,
                    summary_seq=summary.seq,
                    target_seq=target_seq,
                    assistant_seq=message.seq,
                    required_tool_call_ids=required_ids,
                ))

    return moves


async def apply_summary_move(db, session_id: uuid.UUID, move: SummaryMove) -> None:
    """Move one summary after its tool-result group without changing message ids."""
    if move.summary_seq >= move.target_seq:
        return
    max_seq = (
        await db.execute(
            select(func.coalesce(func.max(Message.seq), 0))
            .where(Message.session_id == session_id)
        )
    ).scalar_one()
    temp_seq = int(max_seq) + 1000 + int(move.summary_seq)
    await db.execute(
        update(Message)
        .where(Message.id == move.summary_id)
        .values(seq=temp_seq)
    )
    await db.execute(
        update(Message)
        .where(Message.session_id == session_id)
        .where(Message.seq > move.summary_seq)
        .where(Message.seq <= move.target_seq)
        .values(seq=Message.seq - 1)
    )
    await db.execute(
        update(Message)
        .where(Message.id == move.summary_id)
        .values(seq=move.target_seq)
    )


async def find_reconcilable_child_task_ids(db, session_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(SessionTask)
        .where(SessionTask.session_id == session_id)
        .where(SessionTask.task_kind == "child_session")
        .where(SessionTask.status.in_(["queued", "running", "waiting"]))
        .where(SessionTask.child_session_id.isnot(None))
    )
    tasks = list(rows.scalars().all())
    result: list[str] = []
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
        if latest_run and latest_run.status == "completed" and latest_run.error is None:
            result.append(str(task.id))
    return result


async def find_failed_subtask_continuation(db, session_id: uuid.UUID) -> AgentRun | None:
    rows = await db.execute(
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .where(AgentRun.status == "failed")
        .order_by(AgentRun.created_at.desc())
        .limit(10)
    )
    for run in rows.scalars().all():
        payload = run.payload if isinstance(run.payload, dict) else {}
        if payload.get("is_subtask_continuation") and payload.get("bridged_task_ids"):
            return run
    return None


async def retry_failed_subtask_continuation(
    session_id: uuid.UUID,
    *,
    apply: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        failed_run = await find_failed_subtask_continuation(db, session_id)
        if not failed_run:
            return {"retried": False, "reason": "no_failed_subtask_continuation"}
        failed_run_id = str(failed_run.id)
        failed_payload = failed_run.payload if isinstance(failed_run.payload, dict) else {}
        if await _parent_has_queued_or_active_run(db, session_id):
            return {
                "retried": False,
                "reason": "parent_has_active_run",
                "failed_run_id": failed_run_id,
            }

        parent = await db.get(Session, session_id)
        if not parent:
            return {
                "retried": False,
                "reason": "missing_parent",
                "failed_run_id": failed_run_id,
            }
        parent_session_dir = await _parent_session_dir(db, parent)
        task_ids = [
            str(task_id)
            for task_id in (failed_payload.get("bridged_task_ids") or [])
            if task_id
        ]
        task_rows = await db.execute(
            select(SessionTask)
            .where(SessionTask.session_id == session_id)
            .where(SessionTask.id.in_([uuid.UUID(task_id) for task_id in task_ids]))
        )
        tasks = list(task_rows.scalars().all())

        repair = None
        if apply:
            repair = await repair_parent_checkpoint_before_subtask_continuation(
                db,
                parent_session_id=session_id,
                parent=parent,
                parent_session_dir=parent_session_dir,
                bridged_tasks=tasks,
            )
            run = await _enqueue_parent_continuation(
                db,
                parent,
                session_id,
                parent_session_dir,
                task_ids,
            )
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(status="queued")
            )
            await db.commit()
            return {
                "retried": True,
                "failed_run_id": failed_run_id,
                "enqueued_run_id": str(run.id),
                "bridged_task_ids": task_ids,
                "checkpoint_repair": repair,
            }

        await db.rollback()
        return {
            "retried": False,
            "would_retry": True,
            "failed_run_id": failed_run_id,
            "bridged_task_ids": task_ids,
        }


async def run(session_id: uuid.UUID, *, apply: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        session = await db.get(Session, session_id)
        messages = list((
            await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.seq.asc())
            )
        ).scalars().all())
        before_tail = inspect_db_transcript_tail(messages)
        moves = plan_summary_moves(messages)
        dry_run_task_ids = await find_reconcilable_child_task_ids(db, session_id)
        bridge_result = None
        projection_report = None
        projection_repair = None
        session_status_update = None
        if session is not None:
            current_user = await db.get(User, session.user_id)
            projection_report, _checkpoint_messages = await inspect_session_projection_consistency(
                db,
                session,
                current_user=current_user,
            )

        if apply:
            for move in moves:
                await apply_summary_move(db, session_id, move)
            if projection_report and projection_report.is_db_projection_ahead:
                projection_repair = await repair_db_projection_ahead(db, projection_report)
                if projection_repair.repaired and getattr(session, "status", None) == "error":
                    await db.execute(
                        update(Session)
                        .where(Session.id == session_id)
                        .values(status="idle")
                    )
                    session_status_update = "idle"
                if projection_repair.repaired:
                    await mark_latest_failed_run_projection_recoverable(
                        db,
                        session_id,
                        projection_report,
                        projection_repair,
                    )
            await db.commit()
        else:
            await db.rollback()

    checkpoint_before = await inspect_parent_checkpoint_for_subtask_continuation(session_id)

    bridge_result = await bridge_reconcilable_child_tasks(
        parent_session_id=session_id,
        apply=apply,
    )
    failed_retry_result = await retry_failed_subtask_continuation(session_id, apply=apply)
    checkpoint_after = await inspect_parent_checkpoint_for_subtask_continuation(session_id)

    async with AsyncSessionLocal() as db:
        after_tail = before_tail
        if apply:
            repaired_messages = list((
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.seq.asc())
                )
            ).scalars().all())
            after_tail = inspect_db_transcript_tail(repaired_messages)

        return {
            "session_id": str(session_id),
            "mode": "apply" if apply else "dry-run",
            "planned_summary_moves": [
                {
                    "summary_id": str(move.summary_id),
                    "summary_seq": move.summary_seq,
                    "target_seq": move.target_seq,
                    "assistant_seq": move.assistant_seq,
                    "required_tool_call_ids": move.required_tool_call_ids,
                }
                for move in moves
            ],
            "reconcilable_child_tasks": len(dry_run_task_ids),
            "reconcilable_task_ids": dry_run_task_ids,
            "bridged_task_ids": bridge_result.bridged_task_ids,
            "already_bridged_task_ids": bridge_result.already_bridged_task_ids,
            "completed_task_ids": bridge_result.completed_task_ids,
            "delayed_task_ids": bridge_result.delayed_task_ids,
            "enqueued_run_id": bridge_result.enqueued_run_id,
            "checkpoint_before": _public_checkpoint_report(checkpoint_before),
            "checkpoint_after": _public_checkpoint_report(checkpoint_after),
            "failed_subtask_continuation_retry": failed_retry_result,
            "projection_consistency": (
                projection_report.to_dict() if projection_report is not None else None
            ),
            "projection_repair": (
                projection_repair.to_dict() if projection_repair is not None else None
            ),
            "session_status_update": session_status_update,
            "before": {
                "clean": before_tail.clean,
                "reason": before_tail.reason,
                "open_tool_call_ids": before_tail.open_tool_call_ids,
                "invalid_indices": before_tail.invalid_indices,
            },
            "after": {
                "clean": after_tail.clean,
                "reason": after_tail.reason,
                "open_tool_call_ids": after_tail.open_tool_call_ids,
                "invalid_indices": after_tail.invalid_indices,
            },
        }


def _public_checkpoint_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if not key.startswith("_")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    result = asyncio.run(run(uuid.UUID(args.session_id), apply=bool(args.apply)))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
