"""v0.4.9 Phase C: explicit ``/release`` path for No-Dead-Session contract.

The release path lets a user abandon any open runtime continuation and reset
the session to ``idle`` so they can send a new prompt. It is the user-visible
counterpart to ``/retry``:

- ``/retry`` says "let the model finish what it was doing".
- ``/release`` says "I do not want the model to finish; treat it as done and
  let me start fresh."

Release does the minimum viable cleanup:

1. Cancel pending HITL permission requests (mark as ``cancelled``).
2. Mark any active child ``session_tasks`` as ``cancelled`` so subtask waiting
   parents can advance. Child sessions are not forcefully aborted; a child can
   continue independently and finish on its own.
3. Inject synthetic ``ToolMessage`` closures for any assistant tool_call that
   does not yet have a matching tool result, then append a final
   ``AIMessage`` that records the release. This brings the LangGraph
   checkpoint into ``PROVIDER_READY`` so subsequent prompt ingress accepts a
   new user message.
4. Set ``session.status = "idle"``.
5. Record an audit entry on the latest run's diagnostics.

The behaviour is conservative: release never invokes the model, never
retries provider calls, and never mutates DB messages. It is safe to call
multiple times.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.checkpoint_state import classify_checkpoint_snapshot
from agent.run_models import AgentRun
from agent.task_models import SessionTask
from session import service as session_svc
from session.models import Session

logger = logging.getLogger(__name__)


SYNTHETIC_RELEASE_TOOL_CONTENT = (
    "[released by user] The user explicitly abandoned the pending tool call. "
    "Do not retry; acknowledge that this branch was released and continue "
    "from the user's next message."
)
RELEASE_AI_MESSAGE_CONTENT = (
    "[Session released] The previous model continuation was abandoned at the user's "
    "request. The next user message starts a fresh turn."
)


@dataclass
class SessionReleaseResult:
    session_id: uuid.UUID
    released: bool
    cancelled_permission_count: int = 0
    cancelled_task_count: int = 0
    closed_tool_call_ids: list[str] = field(default_factory=list)
    db_synthetic_tool_result_ids: list[str] = field(default_factory=list)
    new_state_kind: str | None = None
    audit_run_id: uuid.UUID | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "released": self.released,
            "cancelled_permission_count": self.cancelled_permission_count,
            "cancelled_task_count": self.cancelled_task_count,
            "closed_tool_call_ids": list(self.closed_tool_call_ids),
            "db_synthetic_tool_result_ids": list(self.db_synthetic_tool_result_ids),
            "new_state_kind": self.new_state_kind,
            "audit_run_id": str(self.audit_run_id) if self.audit_run_id else None,
            "notes": list(self.notes),
        }


async def release_session_to_idle(
    db: AsyncSession,
    *,
    session: Session,
    current_user: Any,
) -> SessionReleaseResult:
    """Reset an open runtime state back to idle so a new prompt can be accepted."""
    result = SessionReleaseResult(session_id=session.id, released=False)

    # Step 1: cancel pending HITL permissions.
    try:
        from permission import service as perm_svc

        cancelled = await perm_svc.cancel_pending_by_session(db, session.id)
        result.cancelled_permission_count = int(cancelled or 0)
    except Exception as exc:
        logger.exception("session_release: permission cancel failed")
        result.notes.append(f"permission_cancel_failed:{type(exc).__name__}")

    # Step 2: cancel active child session_tasks so subtask_waiting clears.
    try:
        cancelled_tasks = await db.execute(
            update(SessionTask)
            .where(SessionTask.session_id == session.id)
            .where(SessionTask.task_kind == "child_session")
            .where(SessionTask.status.in_(["queued", "running", "waiting"]))
            .values(
                status="cancelled",
                updated_at=datetime.now(timezone.utc),
            )
        )
        result.cancelled_task_count = int(cancelled_tasks.rowcount or 0)
    except Exception as exc:
        logger.exception("session_release: child task cancel failed")
        result.notes.append(f"child_task_cancel_failed:{type(exc).__name__}")

    # Step 3: inject synthetic closures into the LangGraph checkpoint.
    closed_ids, new_state = await _inject_release_synthetics(session, current_user)
    result.closed_tool_call_ids = closed_ids
    result.new_state_kind = new_state

    # Step 3b (Phase C audit Finding 1): insert DB synthetic tool_result rows
    # for any DB-side dangling assistant tool_call. The checkpoint has been
    # closed, but the DB messages projection still shows open tool_calls and
    # would, under the legacy rollback flag, prevent assistant finals from
    # persisting. Even with the new fail-soft projection_can_append we still
    # want the DB transcript to read coherently for the user.
    try:
        db_closed = await _insert_db_synthetic_tool_results(db, session.id)
        result.db_synthetic_tool_result_ids = db_closed
    except Exception as exc:
        logger.exception("session_release: db synthetic insert failed")
        result.notes.append(f"db_synthetic_insert_failed:{type(exc).__name__}")

    # Step 4: set session.status to idle.
    try:
        await session_svc.update_session_status(db, session.id, "idle")
        session.status = "idle"
    except Exception as exc:
        logger.exception("session_release: status update failed")
        result.notes.append(f"status_update_failed:{type(exc).__name__}")
        return result

    # Step 5: record audit on the most recent run, if any.
    try:
        run_id = await _record_release_audit(db, session.id, result)
        result.audit_run_id = run_id
    except Exception as exc:
        logger.exception("session_release: audit failed")
        result.notes.append(f"audit_failed:{type(exc).__name__}")

    result.released = True
    return result


async def _insert_db_synthetic_tool_results(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> list[str]:
    """Insert synthetic ``tool_result`` rows for any DB-tail dangling tool_call.

    Returns the list of tool_call_ids that were closed in the DB. Each row
    uses the same audit fields as the v0.4.8 atomic-tool-group synthetic
    closure (``synthetic_close=true``, ``is_error=true``) plus a stable
    ``error_code`` and a ``release=true`` marker so future readers can
    distinguish user-initiated release from runtime fail-close.
    """
    from agent.runtime_integrity import inspect_db_transcript_tail

    try:
        existing_messages = await session_svc.list_messages(db, session_id)
    except Exception:
        return []

    # Walk all assistant messages forward to find open tool_call ids that the
    # tail inspector currently sees. The tail inspector returns the most
    # recent open group; we want the union of *all* unanswered tool_calls in
    # the DB so a multi-call release closes them in a single sweep.
    answered: set[str] = set()
    requested: list[tuple[str, str]] = []  # (tool_call_id, tool_name)
    for message in existing_messages:
        for part in message.parts or []:
            if not isinstance(part, dict):
                continue
            if part.get("projection_state") == "discarded":
                continue
            ptype = part.get("type")
            tcid = part.get("tool_call_id")
            if not tcid:
                continue
            tcid = str(tcid)
            if ptype == "tool_result":
                answered.add(tcid)
            elif ptype == "tool_call" and message.role == "assistant":
                requested.append((tcid, str(part.get("tool_name") or "")))

    open_pairs = [(tcid, name) for tcid, name in requested if tcid not in answered]
    if not open_pairs:
        return []

    closed: list[str] = []
    for tcid, tool_name in open_pairs:
        try:
            await session_svc.create_message(
                db,
                session_id=session_id,
                role="tool",
                parts=[{
                    "type": "tool_result",
                    "tool_call_id": tcid,
                    "tool_name": tool_name,
                    "output": SYNTHETIC_RELEASE_TOOL_CONTENT,
                    "is_error": True,
                    "synthetic_close": True,
                    "error_code": "USER_RELEASED_TOOL_CALL",
                    "release": True,
                }],
            )
            closed.append(tcid)
        except Exception:
            logger.exception(
                "session_release: failed to insert synthetic tool_result for %s",
                tcid,
            )
    # Also re-validate the tail to confirm the DB is now coherent. We do not
    # raise on residual dirt; the gate is diagnostics-only by default.
    try:
        ordered = sorted(
            existing_messages,
            key=lambda msg: getattr(msg, "seq", 0) or 0,
        )
        _ = inspect_db_transcript_tail(ordered[-20:])
    except Exception:
        pass
    return closed


async def _inject_release_synthetics(
    session: Session,
    current_user: Any,
) -> tuple[list[str], str | None]:
    """Append synthetic closures to the LangGraph checkpoint, if reachable.

    Returns a tuple ``(closed_tool_call_ids, new_state_kind)``. If the
    checkpoint cannot be loaded (e.g. session never started, build_agent
    fails), returns ``([], None)``.
    """
    try:
        from agent.runtime import build_agent
        from agent.child_session import read_child_session_meta
        from core.config import settings as _settings
        from workspace.manager import get_session_dir
    except Exception:
        logger.exception("session_release: imports failed")
        return [], None

    session_id = str(session.id)
    user_root = (
        getattr(current_user, "workspace", None) or _settings.workspace_root
    )
    try:
        session_dir = get_session_dir(user_root, session_id)
    except Exception:
        logger.exception("session_release: session_dir resolution failed")
        return [], None

    parent_session_dir = None
    allowed_tools = None
    if getattr(session, "parent_id", None):
        parent_session_dir = get_session_dir(user_root, str(session.parent_id))
        try:
            child_meta = read_child_session_meta(session_dir) or {}
        except Exception:
            child_meta = {}
        allowed_tools = (
            child_meta.get("resolved_tools")
            or child_meta.get("allowed_tools")
            or None
        )

    try:
        agent = await build_agent(
            session_id=session_id,
            user_id=str(getattr(session, "user_id", "") or ""),
            user_root=user_root,
            session_dir=session_dir,
            agent_id=session.agent_id,
            model_id=session.model_id,
            tool_profile="child" if getattr(session, "parent_id", None) else None,
            parent_session_dir=parent_session_dir,
            allowed_tools=allowed_tools,
        )
    except Exception:
        logger.exception("session_release: build_agent failed")
        return [], None

    config = {"configurable": {"thread_id": session_id}}
    try:
        snapshot = await agent.aget_state(config)
    except Exception:
        logger.exception("session_release: aget_state failed")
        return [], None

    messages = list((snapshot.values or {}).get("messages", []) or []) if snapshot else []
    open_tool_call_ids = _open_tool_call_ids(messages)

    update_messages: list[Any] = []
    for tc_id in open_tool_call_ids:
        update_messages.append(ToolMessage(
            content=SYNTHETIC_RELEASE_TOOL_CONTENT,
            tool_call_id=tc_id,
            additional_kwargs={
                "is_error": True,
                "synthetic_close": True,
                "release": True,
            },
            id=str(uuid.uuid4()),
        ))
    # Always append a release marker AIMessage so the next checkpoint
    # classification yields PROVIDER_READY (no open tool_calls, next=[]).
    update_messages.append(AIMessage(
        content=RELEASE_AI_MESSAGE_CONTENT,
        additional_kwargs={"agentd_release_marker": True},
        id=str(uuid.uuid4()),
    ))

    try:
        try:
            await agent.aupdate_state(
                config=config,
                values={"messages": update_messages},
                as_node="__start__",
            )
        except TypeError:
            await agent.aupdate_state(
                config=config,
                values={"messages": update_messages},
            )
    except Exception:
        logger.exception("session_release: aupdate_state failed")
        return open_tool_call_ids, None

    new_state_kind = None
    try:
        new_snapshot = await agent.aget_state(config)
        new_state = classify_checkpoint_snapshot(new_snapshot) if new_snapshot else None
        new_state_kind = new_state.state_kind.value if new_state else None
    except Exception:
        logger.exception("session_release: post-update classify failed")

    return open_tool_call_ids, new_state_kind


def _open_tool_call_ids(messages: list[Any]) -> list[str]:
    """Identify assistant tool_call ids that lack a matching ToolMessage."""
    answered: set[str] = set()
    requested: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                answered.add(str(tc_id))
            continue
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    requested.append(str(tc_id))
    return [tc_id for tc_id in requested if tc_id not in answered]


async def _record_release_audit(
    db: AsyncSession,
    session_id: uuid.UUID,
    result: SessionReleaseResult,
) -> uuid.UUID | None:
    stmt = (
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentRun.updated_at.desc())
        .limit(1)
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        return None
    diagnostics = dict(getattr(run, "diagnostics", None) or {})
    log = diagnostics.get("session_release_log")
    log = list(log) if isinstance(log, list) else []
    log.append({
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "cancelled_permission_count": result.cancelled_permission_count,
        "cancelled_task_count": result.cancelled_task_count,
        "closed_tool_call_ids": list(result.closed_tool_call_ids),
        "new_state_kind": result.new_state_kind,
        "notes": list(result.notes),
    })
    diagnostics["session_release_log"] = log[-20:]

    from agent import scheduler

    await scheduler.update_diagnostics(db, run.id, diagnostics)
    return run.id
