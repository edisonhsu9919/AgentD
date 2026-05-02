"""Late child-session result bridge helpers.

The bridge truth is the parent ``session_tasks`` row, not the parent's current
UI status. A child can finish after the parent has already returned to idle; in
that case the result must still be written back and a parent continuation
queued exactly once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update as sa_update

from agent.run_models import AgentRun
from agent.task_models import SessionTask
from core.database import AsyncSessionLocal
from session.models import Message, Session


PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]
ACTIVE_TASK_STATUSES = {"queued", "running", "waiting"}
ACTIVE_RUN_STATUSES = {"queued", "claimed", "running"}


@dataclass
class BridgeSweepResult:
    parent_session_id: str | None = None
    bridged_task_ids: list[str] = field(default_factory=list)
    bridged_child_session_ids: list[str] = field(default_factory=list)
    already_bridged_task_ids: list[str] = field(default_factory=list)
    completed_task_ids: list[str] = field(default_factory=list)
    delayed_task_ids: list[str] = field(default_factory=list)
    enqueued_run_id: str | None = None
    skipped_reason: str | None = None
    checkpoint_repair: dict[str, Any] | None = None
    checkpoint_repair_error: str | None = None
    provider_payload_preflight_ok: bool | None = None
    provider_payload_issues: list[dict[str, Any]] = field(default_factory=list)


async def bridge_reconcilable_child_tasks(
    parent_session_id: uuid.UUID | None = None,
    *,
    child_session_id: uuid.UUID | None = None,
    publish: PublishFn | None = None,
    apply: bool = True,
) -> BridgeSweepResult:
    """Bridge terminal child tasks back to their parent.

    If ``child_session_id`` is supplied, only that child is considered and its
    parent is inferred. If ``parent_session_id`` is supplied, all active child
    tasks for that parent are swept.
    """
    async with AsyncSessionLocal() as db:
        if child_session_id is not None and parent_session_id is None:
            child = await db.get(Session, child_session_id)
            if not child or not child.parent_id:
                return BridgeSweepResult(skipped_reason="not_child_session")
            parent_session_id = child.parent_id

        if parent_session_id is None:
            return BridgeSweepResult(skipped_reason="missing_parent_session_id")

        parent = await db.get(Session, parent_session_id)
        if not parent:
            return BridgeSweepResult(
                parent_session_id=str(parent_session_id),
                skipped_reason="missing_parent",
            )
        if getattr(parent, "status", None) == "error":
            return BridgeSweepResult(
                parent_session_id=str(parent_session_id),
                skipped_reason="parent_error",
            )

        rows = await db.execute(
            select(SessionTask)
            .where(SessionTask.session_id == parent_session_id)
            .where(SessionTask.task_kind == "child_session")
            .where(SessionTask.blocking_mode == "blocking")
            .where(SessionTask.status.in_(list(ACTIVE_TASK_STATUSES)))
            .where(SessionTask.child_session_id.isnot(None))
        )
        tasks = list(rows.scalars().all())
        if child_session_id is not None:
            tasks = [task for task in tasks if task.child_session_id == child_session_id]
        if not tasks:
            return BridgeSweepResult(
                parent_session_id=str(parent_session_id),
                skipped_reason="no_active_child_tasks",
            )

        result = BridgeSweepResult(parent_session_id=str(parent_session_id))
        parent_busy = await _parent_has_queued_or_active_run(db, parent_session_id)
        parent_session_dir = await _parent_session_dir(db, parent)
        bridge_writes: list[tuple[SessionTask, list[dict[str, Any]]]] = []
        completion_candidates: list[tuple[SessionTask, str, str]] = []

        for task in tasks:
            child_id = task.child_session_id
            if not child_id:
                continue
            child = await db.get(Session, child_id)
            if not child or child.status != "idle":
                continue
            latest_run = await _latest_run(db, child_id)
            if not latest_run or latest_run.status != "completed" or latest_run.error:
                continue

            task_id = str(task.id)
            child_id_text = str(child_id)
            summary = await _latest_child_summary(db, child_id)
            source_refs = await _child_source_refs(db, child_id)
            already_bridged = await parent_has_subtask_result(
                db,
                parent_session_id,
                task_id,
                child_id_text,
            )

            if not already_bridged:
                parts = [_subtask_result_part(task, child_id_text, summary, source_refs)]
                if source_refs:
                    parts.append({"type": "source_refs", "sources": source_refs})
                if not await _can_append_parent_bridge(db, parent_session_id, parts):
                    result.delayed_task_ids.append(task_id)
                    continue
                bridge_writes.append((task, parts))
                result.bridged_task_ids.append(task_id)
                result.bridged_child_session_ids.append(child_id_text)
            else:
                result.already_bridged_task_ids.append(task_id)

            if parent_busy:
                result.delayed_task_ids.append(task_id)
                continue

            completion_candidates.append((task, summary, child_id_text))

        if completion_candidates and not parent_busy:
            if apply:
                try:
                    repair = await repair_parent_checkpoint_before_subtask_continuation(
                        db,
                        parent_session_id=parent_session_id,
                        parent=parent,
                        parent_session_dir=parent_session_dir,
                        bridged_tasks=[task for task, _, _ in completion_candidates],
                    )
                    result.checkpoint_repair = repair
                    result.provider_payload_preflight_ok = bool(
                        repair.get("provider_payload_preflight_ok", True)
                    )
                    result.provider_payload_issues = list(
                        repair.get("provider_payload_issues") or []
                    )
                except Exception as exc:
                    result.checkpoint_repair_error = str(exc)
                    result.provider_payload_preflight_ok = False
                    for task, _, _ in completion_candidates:
                        task_id = str(task.id)
                        if task_id not in result.delayed_task_ids:
                            result.delayed_task_ids.append(task_id)
                    await db.rollback()
                    return result

                from session import service as session_svc

                for task, parts in bridge_writes:
                    await session_svc.create_message(
                        db,
                        session_id=parent_session_id,
                        role="assistant",
                        parts=parts,
                    )
                for task, summary, child_id_text in completion_candidates:
                    _mark_task_completed(task)
                    if parent_session_dir:
                        _write_task_artifact(parent_session_dir, task, summary, child_id_text)

            result.completed_task_ids.extend(
                str(task.id) for task, _, _ in completion_candidates
            )

        should_enqueue = bool(result.completed_task_ids) and not parent_busy
        if should_enqueue and apply:
            run = await _enqueue_parent_continuation(
                db,
                parent,
                parent_session_id,
                parent_session_dir,
                result.completed_task_ids,
            )
            result.enqueued_run_id = str(run.id)
            await db.execute(
                sa_update(Session)
                .where(Session.id == parent_session_id)
                .values(status="queued")
            )

        if apply:
            await db.commit()
        else:
            await db.rollback()

    if apply and publish and result.enqueued_run_id and result.parent_session_id:
        await publish(result.parent_session_id, {"event": "status_change", "status": "queued"})
        await publish(result.parent_session_id, {
            "event": "task_completed",
            "child_session_ids": result.bridged_child_session_ids,
            "task_ids": result.completed_task_ids,
        })

    return result


async def parent_has_subtask_result(
    db,
    parent_id: uuid.UUID,
    task_id: str,
    child_session_id: str,
) -> bool:
    rows = await db.execute(
        select(Message)
        .where(Message.session_id == parent_id)
        .where(Message.role == "assistant")
        .order_by(Message.seq.desc())
    )
    for message in rows.scalars().all():
        for part in getattr(message, "parts", None) or []:
            if not isinstance(part, dict) or part.get("type") != "subtask_result":
                continue
            if task_id and str(part.get("task_id") or "") == task_id:
                return True
            if child_session_id and str(part.get("child_session_id") or "") == child_session_id:
                return True
    return False


async def inspect_parent_checkpoint_for_subtask_continuation(
    parent_session_id: uuid.UUID,
) -> dict[str, Any]:
    """Inspect parent checkpoint/provider payload readiness for subtask resume."""
    async with AsyncSessionLocal() as db:
        parent = await db.get(Session, parent_session_id)
        if not parent:
            return {"ok": False, "reason": "missing_parent"}
        parent_session_dir = await _parent_session_dir(db, parent)
        tasks = await _parent_child_tasks(db, parent_session_id)
        return await _inspect_parent_checkpoint_for_subtask_continuation(
            db,
            parent_session_id=parent_session_id,
            parent=parent,
            parent_session_dir=parent_session_dir,
            bridged_tasks=tasks,
        )


async def repair_parent_checkpoint_before_subtask_continuation(
    db,
    *,
    parent_session_id: uuid.UUID,
    parent: Session,
    parent_session_dir: str | None,
    bridged_tasks: list[SessionTask],
) -> dict[str, Any]:
    """Repair and preflight parent checkpoint before enqueueing continuation.

    Late child bridge must not enqueue a parent run if the runtime checkpoint
    still contains dangling tool calls from the launch_subagent boundary. This
    mirrors the old worker fallback repair path, but is reusable by worker,
    prompt-ingress repair and the standalone repair script.
    """
    state = await _inspect_parent_checkpoint_for_subtask_continuation(
        db,
        parent_session_id=parent_session_id,
        parent=parent,
        parent_session_dir=parent_session_dir,
        bridged_tasks=bridged_tasks,
    )
    if state.get("checkpoint_valid") and state.get("provider_payload_preflight_ok"):
        return _public_checkpoint_report(state)

    agent = state.get("_agent")
    config = state.get("_config")
    if agent is None or config is None:
        raise RuntimeError(state.get("reason") or "parent_checkpoint_unavailable")

    from agent.executor import (
        _checkpoint_tool_adjacency_is_valid,
        _checkpoint_tool_call_ids,
        _load_tool_messages_from_persisted_session,
        _repair_checkpoint_tool_adjacency,
    )

    messages = state.get("_messages") or []
    needed_tool_call_ids = _checkpoint_tool_call_ids(messages)
    repair_tool_messages = await _load_tool_messages_from_persisted_session(
        str(parent_session_id),
        needed_tool_call_ids,
    )
    repair_tool_messages.extend(
        _synthesize_launch_subagent_tool_messages(
            messages,
            existing_tool_call_ids={
                getattr(msg, "tool_call_id", None)
                for msg in repair_tool_messages
            },
            tasks=bridged_tasks or await _parent_child_tasks(db, parent_session_id),
        )
    )
    repair_result = await _repair_checkpoint_tool_adjacency(
        agent,
        config,
        str(parent_session_id),
        candidate_tool_messages=repair_tool_messages,
        strict=True,
    )

    repaired = await _inspect_loaded_parent_checkpoint(
        agent,
        config,
        parent_session_id=parent_session_id,
        provider_family=getattr(agent, "_provider_family", "openai_compatible"),
        model_id=getattr(parent, "model_id", None),
    )
    repaired["checkpoint_repair"] = repair_result
    if not repaired.get("checkpoint_valid"):
        raise RuntimeError(
            "Parent checkpoint tool adjacency remains invalid after repair: "
            f"{repaired.get('invalid_indices')}"
        )
    if not repaired.get("provider_payload_preflight_ok"):
        raise RuntimeError(
            "Parent provider payload preflight failed after checkpoint repair: "
            f"{repaired.get('provider_payload_issues')}"
        )
    return _public_checkpoint_report(repaired)


def _public_checkpoint_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if not key.startswith("_")
    }


async def _parent_has_queued_or_active_run(db, parent_session_id: uuid.UUID) -> bool:
    run = (
        await db.execute(
            select(AgentRun.id)
            .where(AgentRun.session_id == parent_session_id)
            .where(AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)))
            .limit(1)
        )
    ).scalar_one_or_none()
    return run is not None


async def _parent_child_tasks(db, parent_session_id: uuid.UUID) -> list[SessionTask]:
    rows = await db.execute(
        select(SessionTask)
        .where(SessionTask.session_id == parent_session_id)
        .where(SessionTask.task_kind == "child_session")
        .where(SessionTask.child_session_id.isnot(None))
        .order_by(SessionTask.created_at.asc())
    )
    return list(rows.scalars().all())


async def _inspect_parent_checkpoint_for_subtask_continuation(
    db,
    *,
    parent_session_id: uuid.UUID,
    parent: Session,
    parent_session_dir: str | None,
    bridged_tasks: list[SessionTask],
) -> dict[str, Any]:
    try:
        from auth.models import User
        from agent.runtime import build_agent

        user = await db.get(User, parent.user_id)
        if not user or not parent_session_dir:
            return {
                "ok": False,
                "checkpoint_valid": False,
                "provider_payload_preflight_ok": False,
                "reason": "missing_parent_runtime_context",
            }
        agent = await build_agent(
            session_id=str(parent_session_id),
            user_id=str(parent.user_id),
            user_root=user.workspace,
            session_dir=parent_session_dir,
            agent_id=parent.agent_id,
            model_id=parent.model_id,
        )
        config = {"configurable": {"thread_id": str(parent_session_id)}}
        state = await _inspect_loaded_parent_checkpoint(
            agent,
            config,
            parent_session_id=parent_session_id,
            provider_family=getattr(agent, "_provider_family", "openai_compatible"),
            model_id=parent.model_id,
        )
        state["_agent"] = agent
        state["_config"] = config
        return state
    except Exception as exc:
        return {
            "ok": False,
            "checkpoint_valid": False,
            "provider_payload_preflight_ok": False,
            "reason": str(exc),
        }


async def _inspect_loaded_parent_checkpoint(
    agent,
    config: dict,
    *,
    parent_session_id: uuid.UUID,
    provider_family: str,
    model_id: str | None,
) -> dict[str, Any]:
    from agent.checkpoint_state import find_invalid_tool_adjacency_indices
    from agent.executor import _checkpoint_tool_adjacency_is_valid
    from agent.provider_payload_validator import validate_provider_payload
    from agent.provider_reasoning import _convert_message_to_provider_dict

    snapshot = await agent.aget_state(config)
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    checkpoint_valid = _checkpoint_tool_adjacency_is_valid(messages)
    provider_messages = [
        _convert_message_to_provider_dict(message, provider_family)
        for message in messages
    ]
    validation = validate_provider_payload(
        provider_messages,
        provider_family=provider_family,
        model_id=model_id,
        strict=True,
    )
    return {
        "ok": checkpoint_valid and validation.ok,
        "checkpoint_valid": checkpoint_valid,
        "invalid_indices": find_invalid_tool_adjacency_indices(messages),
        "provider_payload_preflight_ok": validation.ok,
        "provider_payload_issues": validation.issue_dicts,
        "provider_family": provider_family,
        "message_count": len(messages),
        "_messages": messages,
    }


async def _latest_run(db, child_session_id: uuid.UUID) -> AgentRun | None:
    return (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.session_id == child_session_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _parent_session_dir(db, parent: Session) -> str | None:
    try:
        from auth.models import User
        from workspace.manager import get_session_dir

        user = await db.get(User, parent.user_id)
        if not user:
            return None
        return get_session_dir(user.workspace, str(parent.id))
    except Exception:
        return None


async def _latest_child_summary(db, child_session_id: uuid.UUID) -> str:
    row = (
        await db.execute(
            select(Message)
            .where(Message.session_id == child_session_id)
            .where(Message.role == "assistant")
            .order_by(Message.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return ""
    chunks: list[str] = []
    for part in getattr(row, "parts", None) or []:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("content"):
            chunks.append(str(part.get("content")))
        elif isinstance(part, dict) and part.get("type") == "summary" and part.get("summary"):
            chunks.append(str(part.get("summary")))
    return "\n".join(chunks).strip()


async def _child_source_refs(db, child_session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(Message)
        .where(Message.session_id == child_session_id)
        .order_by(Message.seq.asc())
    )
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in rows.scalars().all():
        for part in getattr(message, "parts", None) or []:
            if not isinstance(part, dict) or part.get("type") != "source_refs":
                continue
            for ref in part.get("sources") or []:
                key = str(ref.get("doc_id") or ref.get("source_id") or ref)
                if key in seen:
                    continue
                seen.add(key)
                refs.append(ref)
    return refs


def _subtask_result_part(
    task: SessionTask,
    child_session_id: str,
    summary: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = str(task.id)
    return {
        "type": "subtask_result",
        "task_id": task_id,
        "child_session_id": child_session_id,
        "status": "completed",
        "summary": summary,
        "artifact_root": f".agentd/tasks/{task_id}/artifacts",
        "result_ref": f".agentd/tasks/{task_id}/result.json",
        "title": getattr(task, "title", "") or "",
        "source_refs": source_refs,
    }


def _synthesize_launch_subagent_tool_messages(
    messages: list,
    *,
    existing_tool_call_ids: set[str | None],
    tasks: list[SessionTask],
) -> list:
    """Synthesize missing launch_subagent ToolMessages for checkpoint repair."""
    import json
    from langchain_core.messages import AIMessage, ToolMessage

    available_tasks = list(tasks or [])
    tasks_by_tool_call_id = {
        getattr(task, "tool_call_id", ""): task
        for task in available_tasks
        if getattr(task, "tool_call_id", "")
    }
    tasks_by_title: dict[str, SessionTask] = {}
    for task in available_tasks:
        title = (getattr(task, "title", "") or "").strip()
        if title and title not in tasks_by_title:
            tasks_by_title[title] = task

    synthesized = []
    used_task_ids: set[str] = set()
    for msg in messages:
        from agent.checkpoint_state import ai_message_tool_calls

        tool_calls = ai_message_tool_calls(msg)
        if not isinstance(msg, AIMessage) or not tool_calls:
            continue
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id") or ""
            if (
                not tool_call_id
                or tool_call_id in existing_tool_call_ids
                or tool_call.get("name") != "launch_subagent"
            ):
                continue

            args = tool_call.get("args") if isinstance(tool_call, dict) else {}
            title = ""
            if isinstance(args, dict):
                title = str(args.get("title") or "").strip()
            task = tasks_by_tool_call_id.get(tool_call_id) or tasks_by_title.get(title)
            if task is None:
                task = next(
                    (
                        candidate
                        for candidate in available_tasks
                        if str(getattr(candidate, "id", "")) not in used_task_ids
                    ),
                    None,
                )
            if task is None:
                continue

            used_task_ids.add(str(task.id))
            child_session_id = str(getattr(task, "child_session_id", "") or "")
            payload = {
                "task_id": str(task.id),
                "status": "waiting_for_child",
                "task_kind": "child_session",
                "blocking_mode": "blocking",
                "child_session_id": child_session_id,
                "run_id": str(task.id),
                "title": getattr(task, "title", "") or title,
                "message": (
                    f"Sub-task '{getattr(task, 'title', '') or title or 'child task'}' "
                    "started in child session. Waiting for it to complete..."
                ),
                "checkpoint_repair_synthesized": True,
            }
            synthesized.append(ToolMessage(
                content=json.dumps(payload, ensure_ascii=False),
                tool_call_id=tool_call_id,
                name="launch_subagent",
            ))
            existing_tool_call_ids.add(tool_call_id)
    return synthesized


async def _can_append_parent_bridge(
    db,
    parent_session_id: uuid.UUID,
    parts: list[dict[str, Any]],
) -> bool:
    try:
        from agent.message_persistence import projection_can_append

        return await projection_can_append(db, parent_session_id, "assistant", parts)
    except Exception:
        return True


def _mark_task_completed(task: SessionTask) -> None:
    task.status = "completed"
    task.result_ref = f".agentd/tasks/{task.id}/result.json"


def _write_task_artifact(
    parent_session_dir: str,
    task: SessionTask,
    summary: str,
    child_session_id: str,
) -> None:
    from agent.tasks import init_task_dir, update_task_status, write_task_result

    task_id = str(task.id)
    init_task_dir(parent_session_dir, task_id)
    update_task_status(parent_session_dir, task_id, "completed", result_summary=summary[:500])
    write_task_result(parent_session_dir, task_id, {
        "status": "completed",
        "summary": summary,
        "child_session_id": child_session_id,
        "late_bridge": True,
    })


async def _enqueue_parent_continuation(
    db,
    parent: Session,
    parent_session_id: uuid.UUID,
    parent_session_dir: str | None,
    bridged_task_ids: list[str],
) -> AgentRun:
    from agent import scheduler
    from auth.models import User

    user = await db.get(User, parent.user_id)
    user_root = user.workspace if user else ""
    session_dir = parent_session_dir or ""
    user_message = (
        "[Sub-task results completed]\n\n"
        f"Newly bridged child task ids: {', '.join(bridged_task_ids)}.\n\n"
        "Continue with the main task based on these sub-task results."
    )
    return await scheduler.enqueue_start(
        db,
        session_id=parent_session_id,
        payload={
            "user_message": user_message,
            "user_id": str(parent.user_id),
            "user_root": user_root,
            "session_dir": session_dir,
            "agent_id": parent.agent_id,
            "model_id": parent.model_id,
            "is_subtask_continuation": True,
            "bridged_task_ids": bridged_task_ids,
        },
    )
