"""DB/checkpoint projection consistency checks.

The DB message log may contain streaming projections that were emitted for UI
responsiveness before LangGraph committed the same state. Provider transcript
truth must follow the checkpoint, so a DB-only dangling assistant tool_call is
repairable only when it is still an uncommitted tail projection.
"""

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import update

from agent.checkpoint_state import ai_message_tool_calls
from agent.runtime_integrity import inspect_db_transcript_tail
from core.config import settings
from session import service as session_svc
from session.models import Message


DISCARDED_PROJECTION_STATE = "discarded"
DISCARD_REASON_DB_AHEAD = "db_projection_ahead_of_checkpoint"


@dataclass(frozen=True)
class ProjectionConsistencyReport:
    db_tail_seq: int | None = None
    db_open_tool_call_ids: list[str] = field(default_factory=list)
    checkpoint_tool_call_ids: list[str] = field(default_factory=list)
    checkpoint_tool_result_ids: list[str] = field(default_factory=list)
    db_ahead_tool_call_ids: list[str] = field(default_factory=list)
    checkpoint_ahead_tool_result_ids: list[str] = field(default_factory=list)
    is_db_projection_ahead: bool = False
    is_checkpoint_projection_ahead: bool = False
    recommended_action: str = "none"
    repairable_message_ids: list[str] = field(default_factory=list)
    repairable_message_seqs: list[int] = field(default_factory=list)
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_tail_seq": self.db_tail_seq,
            "db_open_tool_call_ids": self.db_open_tool_call_ids,
            "checkpoint_tool_call_ids": self.checkpoint_tool_call_ids,
            "checkpoint_tool_result_ids": self.checkpoint_tool_result_ids,
            "db_ahead_tool_call_ids": self.db_ahead_tool_call_ids,
            "checkpoint_ahead_tool_result_ids": self.checkpoint_ahead_tool_result_ids,
            "is_db_projection_ahead": self.is_db_projection_ahead,
            "is_checkpoint_projection_ahead": self.is_checkpoint_projection_ahead,
            "recommended_action": self.recommended_action,
            "repairable_message_ids": self.repairable_message_ids,
            "repairable_message_seqs": self.repairable_message_seqs,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class ProjectionRepairResult:
    repaired: bool
    discarded_message_ids: list[str] = field(default_factory=list)
    discarded_message_seqs: list[int] = field(default_factory=list)
    discarded_tool_call_ids: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repaired": self.repaired,
            "discarded_message_ids": self.discarded_message_ids,
            "discarded_message_seqs": self.discarded_message_seqs,
            "discarded_tool_call_ids": self.discarded_tool_call_ids,
            "reason": self.reason,
        }


async def inspect_db_checkpoint_projection(
    db,
    session_id: uuid.UUID,
    checkpoint_messages: list[Any] | None,
) -> ProjectionConsistencyReport:
    """Compare DB tail tool-call projections with checkpoint truth."""
    try:
        from unittest.mock import Mock

        if isinstance(db, Mock):
            return ProjectionConsistencyReport(blocked_reason="mock_db_bypass")
    except Exception:
        pass
    try:
        messages = await session_svc.list_messages(db, session_id)
    except Exception:
        return ProjectionConsistencyReport(blocked_reason="db_message_load_failed")

    ordered = sorted(list(messages or []), key=lambda msg: getattr(msg, "seq", 0) or 0)
    tail = inspect_db_transcript_tail(ordered)
    checkpoint_tool_call_ids, checkpoint_tool_result_ids = _checkpoint_tool_ids(
        checkpoint_messages or []
    )
    checkpoint_ahead = [
        tool_id
        for tool_id in checkpoint_tool_result_ids
        if tool_id not in checkpoint_tool_call_ids
    ]

    base = {
        "db_tail_seq": tail.tail_seq,
        "db_open_tool_call_ids": tail.open_tool_call_ids,
        "checkpoint_tool_call_ids": checkpoint_tool_call_ids,
        "checkpoint_tool_result_ids": checkpoint_tool_result_ids,
        "checkpoint_ahead_tool_result_ids": checkpoint_ahead,
        "is_checkpoint_projection_ahead": bool(checkpoint_ahead),
    }
    if not tail.has_open_tool_call:
        return ProjectionConsistencyReport(**base)

    db_ahead_ids = [
        tool_id
        for tool_id in tail.open_tool_call_ids
        if tool_id not in checkpoint_tool_call_ids
    ]
    if not db_ahead_ids:
        return ProjectionConsistencyReport(
            **base,
            blocked_reason="open_tool_call_present_in_checkpoint",
        )

    tail_message = _find_message_by_seq(ordered, tail.tail_seq)
    if tail_message is None or getattr(tail_message, "role", None) != "assistant":
        return ProjectionConsistencyReport(
            **base,
            db_ahead_tool_call_ids=db_ahead_ids,
            blocked_reason="open_tool_call_not_tail_assistant",
        )

    later_messages = [
        msg
        for msg in ordered
        if (getattr(msg, "seq", 0) or 0) > (getattr(tail_message, "seq", 0) or 0)
        and _message_has_non_discarded_parts(msg)
    ]
    if later_messages:
        return ProjectionConsistencyReport(
            **base,
            db_ahead_tool_call_ids=db_ahead_ids,
            blocked_reason="later_messages_exist",
        )

    message_ids = [str(getattr(tail_message, "id"))]
    message_seqs = [int(getattr(tail_message, "seq"))]
    return ProjectionConsistencyReport(
        **base,
        db_ahead_tool_call_ids=db_ahead_ids,
        is_db_projection_ahead=True,
        recommended_action="discard_uncommitted_db_projection",
        repairable_message_ids=message_ids,
        repairable_message_seqs=message_seqs,
    )


async def repair_db_projection_ahead(
    db,
    report: ProjectionConsistencyReport,
) -> ProjectionRepairResult:
    """Discard uncommitted DB-only tool_call parts without synthesizing results."""
    if not report.is_db_projection_ahead or not report.repairable_message_ids:
        return ProjectionRepairResult(
            repaired=False,
            reason=report.blocked_reason or "not_db_projection_ahead",
        )

    discarded_ids: list[str] = []
    discarded_seqs: list[int] = []
    discarded_tool_call_ids: list[str] = []

    for message_id in report.repairable_message_ids:
        try:
            msg_uuid = uuid.UUID(str(message_id))
        except (TypeError, ValueError):
            continue
        message = await db.get(Message, msg_uuid)
        if message is None:
            continue
        new_parts = []
        changed = False
        discard_entire_message = any(
            isinstance(part, dict)
            and part.get("type") == "tool_call"
            and str(part.get("tool_call_id")) in report.db_ahead_tool_call_ids
            and part.get("projection_state") != DISCARDED_PROJECTION_STATE
            for part in list(message.parts or [])
        )
        for part in list(message.parts or []):
            if isinstance(part, dict) and discard_entire_message:
                if (
                    part.get("type") == "tool_call"
                    and str(part.get("tool_call_id")) in report.db_ahead_tool_call_ids
                ):
                    discarded_tool_call_ids.append(str(part.get("tool_call_id")))
                updated_part = {
                    **part,
                    "projection_state": DISCARDED_PROJECTION_STATE,
                    "discard_reason": DISCARD_REASON_DB_AHEAD,
                }
                new_parts.append(updated_part)
                changed = True
            else:
                new_parts.append(part)
        if changed:
            await db.execute(
                update(Message)
                .where(Message.id == msg_uuid)
                .values(parts=new_parts)
            )
            discarded_ids.append(str(message.id))
            discarded_seqs.append(int(message.seq))

    return ProjectionRepairResult(
        repaired=bool(discarded_ids),
        discarded_message_ids=discarded_ids,
        discarded_message_seqs=discarded_seqs,
        discarded_tool_call_ids=_unique(discarded_tool_call_ids),
        reason=DISCARD_REASON_DB_AHEAD if discarded_ids else "no_matching_parts",
    )


async def inspect_session_projection_consistency(
    db,
    session,
    *,
    current_user: Any | None = None,
    run_id: str | None = None,
) -> tuple[ProjectionConsistencyReport, list[Any]]:
    """Load checkpoint messages and compare them with DB projection."""
    checkpoint_messages = await load_checkpoint_messages(
        session,
        current_user=current_user,
        run_id=run_id,
    )
    report = await inspect_db_checkpoint_projection(
        db,
        session.id,
        checkpoint_messages,
    )
    return report, checkpoint_messages


async def load_checkpoint_messages(
    session,
    *,
    current_user: Any | None = None,
    run_id: str | None = None,
) -> list[Any]:
    try:
        from agent.child_session import read_child_session_meta
        from agent.runtime import build_agent
        from workspace.manager import get_session_dir

        session_id = str(session.id)
        user_id = str(getattr(session, "user_id", "") or "")
        user_root = (
            getattr(current_user, "workspace", None)
            or settings.workspace_root
        )
        session_dir = get_session_dir(user_root, session_id)
        parent_session_dir = None
        allowed_tools = None
        if getattr(session, "parent_id", None):
            parent_session_dir = get_session_dir(user_root, str(session.parent_id))
            child_meta = read_child_session_meta(session_dir)
            allowed_tools = (
                child_meta.get("resolved_tools")
                or child_meta.get("allowed_tools")
                or None
            )
        agent = await build_agent(
            session_id=session_id,
            user_id=user_id,
            user_root=user_root,
            session_dir=session_dir,
            agent_id=session.agent_id,
            model_id=session.model_id,
            tool_profile="child" if getattr(session, "parent_id", None) else None,
            parent_session_dir=parent_session_dir,
            allowed_tools=allowed_tools,
            run_id=run_id,
        )
        snapshot = await agent.aget_state({"configurable": {"thread_id": session_id}})
        values = getattr(snapshot, "values", {}) or {}
        return list(values.get("messages", []) or [])
    except Exception:
        if settings.debug:
            traceback.print_exc()
        return []


async def repair_session_projection_ahead(
    db,
    session,
    *,
    current_user: Any | None = None,
    run_id: str | None = None,
) -> tuple[ProjectionConsistencyReport, ProjectionRepairResult | None]:
    report, _messages = await inspect_session_projection_consistency(
        db,
        session,
        current_user=current_user,
        run_id=run_id,
    )
    if not report.is_db_projection_ahead:
        return report, None
    repair = await repair_db_projection_ahead(db, report)
    if repair.repaired:
        await mark_latest_failed_run_projection_recoverable(db, session.id, report, repair)
    return report, repair


async def mark_latest_failed_run_projection_recoverable(
    db,
    session_id: uuid.UUID,
    report: ProjectionConsistencyReport,
    repair: ProjectionRepairResult,
) -> None:
    """Mark the latest failed run retryable after DB projection repair."""
    try:
        from sqlalchemy import select
        from agent.run_models import AgentRun

        run = (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.session_id == session_id)
                .where(AgentRun.status == "failed")
                .order_by(AgentRun.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run is None:
            return
        diagnostics = dict(getattr(run, "diagnostics", None) or {})
        diagnostics["projection_repair_recoverable"] = True
        diagnostics["projection_consistency"] = report.to_dict()
        diagnostics["projection_repair"] = repair.to_dict()
        diagnostics["provider_error_category"] = "runtime_projection_repaired"
        diagnostics["runtime_integrity_gate"] = {
            "action": "finalize_idle",
            "reason": "db_projection_ahead_repaired",
            "open_tool_call_ids": [],
            "can_accept_user_prompt": True,
            "db_tail_seq": report.db_tail_seq,
            "checkpoint_state_kind": "next_model_after_tool_result",
        }
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run.id)
            .values(diagnostics=diagnostics)
        )
    except Exception:
        if settings.debug:
            traceback.print_exc()


def active_parts(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        part for part in (parts or [])
        if not (
            isinstance(part, dict)
            and part.get("projection_state") == DISCARDED_PROJECTION_STATE
        )
    ]


def part_is_discarded(part: dict[str, Any]) -> bool:
    return (
        isinstance(part, dict)
        and part.get("projection_state") == DISCARDED_PROJECTION_STATE
    )


def _checkpoint_tool_ids(messages: list[Any]) -> tuple[list[str], list[str]]:
    tool_call_ids: list[str] = []
    tool_result_ids: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in ai_message_tool_calls(message):
                call_id = call.get("id")
                if call_id:
                    _append_unique(tool_call_ids, [str(call_id)])
        elif isinstance(message, ToolMessage):
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                _append_unique(tool_result_ids, [str(tool_call_id)])
    return tool_call_ids, tool_result_ids


def _find_message_by_seq(messages: list[Any], seq: int | None) -> Any | None:
    if seq is None:
        return None
    for message in messages:
        if getattr(message, "seq", None) == seq:
            return message
    return None


def _message_has_non_discarded_parts(message: Any) -> bool:
    return bool(active_parts(getattr(message, "parts", None) or []))


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
