"""Runtime integrity gate for terminal and prompt-ingress decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from sqlalchemy import select

from agent.checkpoint_state import CheckpointState, CheckpointStateKind
from core.config import settings


class RuntimeGateAction(str, Enum):
    FINALIZE_IDLE = "finalize_idle"
    ENTER_WAITING = "enter_waiting"
    ENTER_SUBTASK_WAITING = "enter_subtask_waiting"
    CONTINUE_MODEL = "continue_model"
    FAIL_INTEGRITY_ERROR = "fail_integrity_error"
    REJECT_NEW_PROMPT = "reject_new_prompt"


@dataclass(frozen=True)
class TranscriptTailState:
    has_open_tool_call: bool
    open_tool_call_ids: list[str] = field(default_factory=list)
    invalid_user_inserted_between_tool_group: bool = False
    invalid_indices: list[int] = field(default_factory=list)
    tail_seq: int | None = None
    reason: str = "clean"

    @property
    def clean(self) -> bool:
        return (
            not self.has_open_tool_call
            and not self.invalid_user_inserted_between_tool_group
            and not self.invalid_indices
        )


@dataclass(frozen=True)
class ValidationMessageSlice:
    messages: list[Any]
    scope: str
    start_seq: int | None = None
    end_seq: int | None = None
    run_start_seq: int | None = None
    expanded_from_seq: int | None = None


@dataclass(frozen=True)
class RuntimeIntegrityInput:
    session_id: str
    session_status: str | None
    checkpoint_state: CheckpointState | None
    db_tail_messages: list[Any] = field(default_factory=list)
    pending_permissions: list[Any] = field(default_factory=list)
    latest_run_type: str | None = None
    latest_run_status: str | None = None
    latest_error: str | None = None
    run_start_seq: int | None = None
    run_end_seq: int | None = None
    validation_scope: str | None = None
    expanded_from_seq: int | None = None
    full_confirmation_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeGateDecision:
    action: RuntimeGateAction
    reason: str
    open_tool_call_ids: list[str] = field(default_factory=list)
    checkpoint_state_kind: str | None = None
    is_provider_payload_ready: bool = False
    requires_human_input: bool = False
    can_accept_user_prompt: bool = False
    db_tail_seq: int | None = None
    invalid_indices: list[int] = field(default_factory=list)
    pending_permission_count: int = 0
    validation_scope: str | None = None
    run_start_seq: int | None = None
    run_end_seq: int | None = None
    expanded_from_seq: int | None = None
    full_confirmation_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "open_tool_call_ids": self.open_tool_call_ids,
            "checkpoint_state_kind": self.checkpoint_state_kind,
            "is_provider_payload_ready": self.is_provider_payload_ready,
            "requires_human_input": self.requires_human_input,
            "can_accept_user_prompt": self.can_accept_user_prompt,
            "db_tail_seq": self.db_tail_seq,
            "invalid_indices": self.invalid_indices,
            "pending_permission_count": self.pending_permission_count,
            "validation_scope": self.validation_scope,
            "run_start_seq": self.run_start_seq,
            "run_end_seq": self.run_end_seq,
            "expanded_from_seq": self.expanded_from_seq,
            "full_confirmation_result": self.full_confirmation_result,
        }


class RuntimeIntegrityError(RuntimeError):
    """Raised when a run attempts to finish with unresolved runtime state."""

    def __init__(self, session_id: str, decision: RuntimeGateDecision):
        self.session_id = session_id
        self.decision = decision
        super().__init__(
            "Runtime integrity gate failed: "
            f"session_id={session_id} "
            f"action={decision.action.value} "
            f"reason={decision.reason} "
            f"checkpoint_state_kind={decision.checkpoint_state_kind} "
            f"open_tool_call_ids={decision.open_tool_call_ids} "
            f"db_tail_seq={decision.db_tail_seq} "
            f"validation_scope={decision.validation_scope} "
            f"run_start_seq={decision.run_start_seq}"
        )


class RuntimeIntegrityGate:
    """Pure decision layer for runtime terminal and prompt-ingress states."""

    @classmethod
    def decide_terminal(cls, payload: RuntimeIntegrityInput) -> RuntimeGateDecision:
        # v0.4.9 Phase A: DB tail is diagnostics-only by default. Terminal decisions
        # are driven by checkpoint / runtime state truth. The flag exists as a
        # short-term rollback switch and is planned for removal in v0.5.0.
        tail = inspect_db_transcript_tail(payload.db_tail_messages)
        db_tail_can_fail = settings.runtime_integrity_gate_db_tail_enabled
        state = payload.checkpoint_state
        pending_count = len(payload.pending_permissions or [])
        state_kind = state.state_kind if state else None

        def decision(
            action: RuntimeGateAction,
            reason: str,
            *,
            can_accept_user_prompt: bool = False,
            open_ids: list[str] | None = None,
            checkpoint_state_kind_override: str | None = None,
        ) -> RuntimeGateDecision:
            merged_open_ids = _unique([
                *(open_ids or []),
                *tail.open_tool_call_ids,
                *((state.open_tool_call_ids if state else []) or []),
                *((state.orphan_tool_call_ids if state else []) or []),
                *((state.orphan_tool_message_ids if state else []) or []),
            ])
            return RuntimeGateDecision(
                action=action,
                reason=reason,
                open_tool_call_ids=merged_open_ids,
                checkpoint_state_kind=(
                    checkpoint_state_kind_override
                    or (state_kind.value if state_kind else None)
                ),
                is_provider_payload_ready=bool(
                    state and state.is_provider_payload_ready
                ),
                requires_human_input=bool(
                    (state and state.requires_human_input)
                    or action == RuntimeGateAction.ENTER_WAITING
                ),
                can_accept_user_prompt=can_accept_user_prompt,
                db_tail_seq=tail.tail_seq,
                invalid_indices=tail.invalid_indices,
                pending_permission_count=pending_count,
                validation_scope=payload.validation_scope,
                run_start_seq=payload.run_start_seq,
                run_end_seq=payload.run_end_seq,
                expanded_from_seq=payload.expanded_from_seq,
                full_confirmation_result=payload.full_confirmation_result,
            )

        if payload.session_status == "subtask_waiting":
            return decision(
                RuntimeGateAction.ENTER_SUBTASK_WAITING,
                "session_subtask_waiting",
            )

        if tail.invalid_user_inserted_between_tool_group and db_tail_can_fail:
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                "db_tail_user_inserted_between_tool_group",
            )

        pending_ids = _pending_permission_tool_call_ids(payload.pending_permissions)
        potentially_hitl_open_ids = _unique([
            *tail.open_tool_call_ids,
            *((state.open_tool_call_ids if state else []) or []),
            *((state.orphan_tool_call_ids if state else []) or []),
        ])
        if (
            pending_count > 0
            and potentially_hitl_open_ids
            and set(potentially_hitl_open_ids).issubset(pending_ids)
        ):
            return decision(
                RuntimeGateAction.ENTER_WAITING,
                "hitl_open_tool_call_waiting",
                open_ids=potentially_hitl_open_ids,
                checkpoint_state_kind_override=CheckpointStateKind.HITL_OPEN_TOOL_CALL.value,
            )

        if state and not state.checkpoint_valid:
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                f"checkpoint_invalid:{state.state_kind.value}",
            )

        if tail.has_open_tool_call and db_tail_can_fail:
            if state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL:
                hitl_open_ids = _unique([
                    *tail.open_tool_call_ids,
                    *((state.open_tool_call_ids if state else []) or []),
                    *((state.orphan_tool_call_ids if state else []) or []),
                ])
                if pending_count > 0 and set(hitl_open_ids).issubset(pending_ids):
                    return decision(
                        RuntimeGateAction.ENTER_WAITING,
                        "hitl_open_tool_call_waiting",
                        open_ids=hitl_open_ids,
                        checkpoint_state_kind_override=CheckpointStateKind.HITL_OPEN_TOOL_CALL.value,
                    )
                return decision(
                    RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                    "hitl_open_tool_call_missing_pending_permission",
                )
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                f"db_tail_open_tool_call:{tail.reason}",
            )

        if pending_count > 0:
            if state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL:
                hitl_open_ids = _unique([
                    *tail.open_tool_call_ids,
                    *((state.open_tool_call_ids if state else []) or []),
                    *((state.orphan_tool_call_ids if state else []) or []),
                ])
                if hitl_open_ids and set(hitl_open_ids).issubset(pending_ids):
                    return decision(
                        RuntimeGateAction.ENTER_WAITING,
                        "hitl_open_tool_call_waiting",
                        open_ids=hitl_open_ids,
                        checkpoint_state_kind_override=CheckpointStateKind.HITL_OPEN_TOOL_CALL.value,
                    )
                return decision(
                    RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                    "pending_permission_without_matching_open_hitl_checkpoint",
                )
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                "pending_permission_without_open_hitl_checkpoint",
            )

        if state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL:
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                "hitl_open_tool_call_missing_pending_permission",
            )

        if state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT:
            if cls._is_recoverable_provider_boundary(payload):
                return decision(
                    RuntimeGateAction.CONTINUE_MODEL,
                    "provider_failure_after_closed_tool_result",
                )
            return decision(
                RuntimeGateAction.CONTINUE_MODEL,
                    "checkpoint_next_model_after_tool_result",
                )

        if state is None:
            if pending_count == 0:
                return decision(
                    RuntimeGateAction.FINALIZE_IDLE,
                    "no_checkpoint_clean_db_tail" if tail.clean else "no_checkpoint_db_tail_diagnostics_only",
                    can_accept_user_prompt=True,
                )
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                "missing_checkpoint_with_runtime_state",
            )

        if state_kind in {CheckpointStateKind.EMPTY, CheckpointStateKind.PROVIDER_READY}:
            return decision(
                RuntimeGateAction.FINALIZE_IDLE,
                "checkpoint_clean" if tail.clean else "checkpoint_clean_db_tail_diagnostics_only",
                can_accept_user_prompt=True,
            )

        if state and _checkpoint_has_active_next(state):
            return decision(
                RuntimeGateAction.FAIL_INTEGRITY_ERROR,
                "checkpoint_has_active_next",
            )

        return decision(
            RuntimeGateAction.FAIL_INTEGRITY_ERROR,
            f"unsupported_checkpoint_state:{state.state_kind.value}",
        )

    @classmethod
    def decide_prompt_ingress(cls, payload: RuntimeIntegrityInput) -> RuntimeGateDecision:
        checkpoint_decision = cls._checkpoint_truth_prompt_decision(payload)
        if checkpoint_decision is not None:
            return checkpoint_decision

        terminal = cls.decide_terminal(payload)
        if terminal.action == RuntimeGateAction.FINALIZE_IDLE:
            return terminal
        return RuntimeGateDecision(
            action=RuntimeGateAction.REJECT_NEW_PROMPT,
            reason=terminal.reason,
            open_tool_call_ids=terminal.open_tool_call_ids,
            checkpoint_state_kind=terminal.checkpoint_state_kind,
            is_provider_payload_ready=terminal.is_provider_payload_ready,
            requires_human_input=terminal.requires_human_input,
            can_accept_user_prompt=False,
            db_tail_seq=terminal.db_tail_seq,
            invalid_indices=terminal.invalid_indices,
            pending_permission_count=terminal.pending_permission_count,
            validation_scope=terminal.validation_scope,
            run_start_seq=terminal.run_start_seq,
            run_end_seq=terminal.run_end_seq,
            expanded_from_seq=terminal.expanded_from_seq,
            full_confirmation_result=terminal.full_confirmation_result,
        )

    @classmethod
    def _checkpoint_truth_prompt_decision(
        cls,
        payload: RuntimeIntegrityInput,
    ) -> RuntimeGateDecision | None:
        """Allow prompt ingress from clean checkpoint truth despite dirty DB projection."""
        state = payload.checkpoint_state
        if state is None or not state.checkpoint_valid:
            return None
        if payload.session_status in {"queued", "running", "waiting", "subtask_waiting"}:
            return None
        if payload.pending_permissions:
            return None
        if state.state_kind not in {
            CheckpointStateKind.EMPTY,
            CheckpointStateKind.PROVIDER_READY,
        }:
            return None
        if _checkpoint_has_active_next(state) and state.state_kind != CheckpointStateKind.PROVIDER_READY:
            return None

        tail = inspect_db_transcript_tail(payload.db_tail_messages)
        return RuntimeGateDecision(
            action=RuntimeGateAction.FINALIZE_IDLE,
            reason="checkpoint_clean_prompt_ingress",
            open_tool_call_ids=[],
            checkpoint_state_kind=state.state_kind.value,
            is_provider_payload_ready=state.is_provider_payload_ready,
            requires_human_input=False,
            can_accept_user_prompt=True,
            db_tail_seq=tail.tail_seq,
            invalid_indices=tail.invalid_indices,
            pending_permission_count=0,
            validation_scope=payload.validation_scope,
            run_start_seq=payload.run_start_seq,
            run_end_seq=payload.run_end_seq,
            expanded_from_seq=payload.expanded_from_seq,
            full_confirmation_result=payload.full_confirmation_result,
        )

    @staticmethod
    def _is_recoverable_provider_boundary(payload: RuntimeIntegrityInput) -> bool:
        text = (payload.latest_error or "").lower()
        return (
            "timeout" in text
            or "readtimeout" in text
            or "connection" in text
            or "connecterror" in text
        )


def inspect_db_transcript_tail(messages: list[Any]) -> TranscriptTailState:
    ordered = sorted(list(messages or []), key=lambda msg: getattr(msg, "seq", 0) or 0)
    if not ordered:
        return TranscriptTailState(has_open_tool_call=False, tail_seq=None)

    i = 0
    invalid_indices: list[int] = []
    tail_seq = getattr(ordered[-1], "seq", None)
    while i < len(ordered):
        msg = ordered[i]
        role = getattr(msg, "role", None)
        parts = list(getattr(msg, "parts", None) or [])
        tool_calls = _tool_call_parts(parts)

        if role == "assistant" and tool_calls:
            required_ids = [
                str(part.get("tool_call_id"))
                for part in tool_calls
                if part.get("tool_call_id")
            ]
            tool_ids: list[str] = []
            j = i + 1
            while j < len(ordered) and getattr(ordered[j], "role", None) == "tool":
                tool_ids.extend(_tool_result_ids(getattr(ordered[j], "parts", None) or []))
                j += 1

            missing = [tool_id for tool_id in required_ids if tool_id not in tool_ids]
            extra = [tool_id for tool_id in tool_ids if tool_id not in required_ids]
            partial = bool(tool_ids) and bool(missing)

            if missing or extra:
                invalid_indices.extend(_message_seq_slice(ordered, i, j))
                next_msg = ordered[j] if j < len(ordered) else None
                next_role = getattr(next_msg, "role", None) if next_msg is not None else None
                user_inserted = next_role == "user"
                return TranscriptTailState(
                    has_open_tool_call=bool(missing),
                    open_tool_call_ids=_unique(missing + extra),
                    invalid_user_inserted_between_tool_group=user_inserted,
                    invalid_indices=_unique_int(invalid_indices),
                    tail_seq=getattr(msg, "seq", tail_seq),
                    reason=(
                        "user_inserted_between_tool_group"
                        if user_inserted
                        else "partial_tool_group_closed"
                        if partial
                        else "assistant_tool_call_missing_tool_result"
                        if missing
                        else "unknown_tool_result_id"
                    ),
                )
            i = max(j, i + 1)
            continue

        if role == "tool":
            tool_ids = _tool_result_ids(parts)
            return TranscriptTailState(
                has_open_tool_call=False,
                open_tool_call_ids=tool_ids,
                invalid_indices=[getattr(msg, "seq", i)],
                tail_seq=getattr(msg, "seq", tail_seq),
                reason="orphan_tool_message",
            )

        i += 1

    return TranscriptTailState(has_open_tool_call=False, tail_seq=tail_seq)


async def load_recent_db_messages(db, session_id: uuid.UUID, *, limit: int = 20) -> list[Any]:
    from session.models import Message

    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(reversed(rows))


async def load_terminal_validation_messages(
    db,
    session_id: uuid.UUID,
    *,
    run_start_seq: int | None,
    boundary_expand: bool = True,
    fallback_limit: int = 20,
) -> ValidationMessageSlice:
    from session.models import Message

    if run_start_seq is None:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.seq.desc())
            .limit(fallback_limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        messages = list(reversed(rows))
        scope = "recent_fallback"
    else:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .where(Message.seq > run_start_seq)
            .order_by(Message.seq.asc())
        )
        messages = list((await db.execute(stmt)).scalars().all())
        scope = "run_slice"

    validation = _validation_slice_from_messages(
        messages,
        scope=scope,
        run_start_seq=run_start_seq,
    )
    if not boundary_expand:
        return validation
    return await _expand_validation_boundary(db, session_id, validation)


async def load_full_validation_messages(
    db,
    session_id: uuid.UUID,
) -> ValidationMessageSlice:
    from session.models import Message

    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq.asc())
    )
    messages = list((await db.execute(stmt)).scalars().all())
    return _validation_slice_from_messages(messages, scope="full_confirmation")


def build_validation_message_slice(
    messages: list[Any],
    *,
    run_start_seq: int | None,
    fallback_limit: int = 20,
    boundary_expand: bool = True,
) -> ValidationMessageSlice:
    """Pure helper used by tests; DB loader performs the same boundary expansion."""
    ordered = sorted(list(messages or []), key=lambda msg: getattr(msg, "seq", 0) or 0)
    if run_start_seq is None:
        base = ordered[-fallback_limit:]
        scope = "recent_fallback"
    else:
        base = [msg for msg in ordered if (getattr(msg, "seq", 0) or 0) > run_start_seq]
        scope = "run_slice"
    validation = _validation_slice_from_messages(
        base,
        scope=scope,
        run_start_seq=run_start_seq,
    )
    if not boundary_expand:
        return validation
    return _expand_validation_boundary_from_all_messages(ordered, validation)


async def decide_terminal_with_layered_validation(
    db,
    *,
    session_id: uuid.UUID,
    session_status: str | None,
    checkpoint_state: CheckpointState | None,
    pending_permissions: list[Any],
    run_start_seq: int | None,
    latest_run_type: str | None = None,
    latest_run_status: str | None = None,
    latest_error: str | None = None,
) -> tuple[RuntimeGateDecision, dict[str, Any] | None]:
    validation = await load_terminal_validation_messages(
        db,
        session_id,
        run_start_seq=run_start_seq,
    )
    payload = RuntimeIntegrityInput(
        session_id=str(session_id),
        session_status=session_status,
        checkpoint_state=checkpoint_state,
        db_tail_messages=validation.messages,
        pending_permissions=pending_permissions,
        latest_run_type=latest_run_type,
        latest_run_status=latest_run_status,
        latest_error=latest_error,
        run_start_seq=run_start_seq,
        run_end_seq=validation.end_seq,
        validation_scope=validation.scope,
        expanded_from_seq=validation.expanded_from_seq,
    )
    decision = RuntimeIntegrityGate.decide_terminal(payload)
    warning = None

    if decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR:
        full_validation = await load_full_validation_messages(db, session_id)
        full_payload = replace(
            payload,
            db_tail_messages=full_validation.messages,
            validation_scope=full_validation.scope,
            run_end_seq=full_validation.end_seq,
            expanded_from_seq=full_validation.expanded_from_seq,
            full_confirmation_result=None,
        )
        full_decision = RuntimeIntegrityGate.decide_terminal(full_payload)
        full_result = {
            "action": full_decision.action.value,
            "reason": full_decision.reason,
            "validation_scope": full_decision.validation_scope,
            "db_tail_seq": full_decision.db_tail_seq,
            "open_tool_call_ids": full_decision.open_tool_call_ids,
        }
        if full_decision.action == RuntimeGateAction.FINALIZE_IDLE:
            warning = {
                "original_reason": decision.reason,
                "original_scope": decision.validation_scope,
                "resolved_by": "full_confirmation",
                "full_confirmation_result": full_result,
            }
            decision = replace(
                full_decision,
                reason="full_confirmation_clean_after_layered_failure",
                full_confirmation_result=full_result,
            )
        else:
            decision = replace(
                decision,
                full_confirmation_result=full_result,
            )

    return decision, warning


async def persist_runtime_integrity_diagnostics(
    db,
    run_id: uuid.UUID | None,
    decision: RuntimeGateDecision,
    warning: dict[str, Any] | None = None,
) -> None:
    if run_id is None:
        return
    try:
        from agent import scheduler
        from agent.run_models import AgentRun

        run = await db.get(AgentRun, run_id)
        existing = dict(getattr(run, "diagnostics", None) or {}) if run else {}
        existing["runtime_integrity_gate"] = decision.to_dict()
        if warning:
            existing["runtime_integrity_warning"] = warning
        await scheduler.update_diagnostics(db, run_id, existing)
    except Exception:
        return


def _validation_slice_from_messages(
    messages: list[Any],
    *,
    scope: str,
    run_start_seq: int | None = None,
    expanded_from_seq: int | None = None,
) -> ValidationMessageSlice:
    ordered = sorted(list(messages or []), key=lambda msg: getattr(msg, "seq", 0) or 0)
    return ValidationMessageSlice(
        messages=ordered,
        scope=scope,
        start_seq=getattr(ordered[0], "seq", None) if ordered else None,
        end_seq=getattr(ordered[-1], "seq", None) if ordered else None,
        run_start_seq=run_start_seq,
        expanded_from_seq=expanded_from_seq,
    )


async def _expand_validation_boundary(
    db,
    session_id: uuid.UUID,
    validation: ValidationMessageSlice,
) -> ValidationMessageSlice:
    messages = validation.messages
    if not messages or getattr(messages[0], "role", None) != "tool":
        return validation

    first = messages[0]
    first_seq = getattr(first, "seq", None)
    first_ids = set(_tool_result_ids(getattr(first, "parts", None) or []))
    if not first_ids or first_seq is None:
        return validation

    from session.models import Message

    prior_stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.seq < first_seq)
        .order_by(Message.seq.desc())
        .limit(100)
    )
    prior_messages = list((await db.execute(prior_stmt)).scalars().all())
    assistant = _find_boundary_assistant(prior_messages, first_ids)
    if assistant is None:
        return validation

    end_seq = validation.end_seq if validation.end_seq is not None else first_seq
    expanded_stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.seq >= getattr(assistant, "seq"))
        .where(Message.seq <= end_seq)
        .order_by(Message.seq.asc())
    )
    expanded = list((await db.execute(expanded_stmt)).scalars().all())
    return _validation_slice_from_messages(
        expanded,
        scope="boundary_expanded",
        run_start_seq=validation.run_start_seq,
        expanded_from_seq=getattr(assistant, "seq", None),
    )


def _expand_validation_boundary_from_all_messages(
    ordered: list[Any],
    validation: ValidationMessageSlice,
) -> ValidationMessageSlice:
    messages = validation.messages
    if not messages or getattr(messages[0], "role", None) != "tool":
        return validation

    first = messages[0]
    first_seq = getattr(first, "seq", None)
    first_ids = set(_tool_result_ids(getattr(first, "parts", None) or []))
    if not first_ids or first_seq is None:
        return validation

    prior = [msg for msg in ordered if (getattr(msg, "seq", 0) or 0) < first_seq]
    assistant = _find_boundary_assistant(reversed(prior), first_ids)
    if assistant is None:
        return validation

    assistant_seq = getattr(assistant, "seq", None)
    end_seq = validation.end_seq if validation.end_seq is not None else first_seq
    expanded = [
        msg for msg in ordered
        if assistant_seq is not None
        and assistant_seq <= (getattr(msg, "seq", 0) or 0) <= end_seq
    ]
    return _validation_slice_from_messages(
        expanded,
        scope="boundary_expanded",
        run_start_seq=validation.run_start_seq,
        expanded_from_seq=assistant_seq,
    )


def _find_boundary_assistant(messages: Any, first_tool_ids: set[str]) -> Any | None:
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        tool_ids = {
            str(part.get("tool_call_id"))
            for part in _tool_call_parts(getattr(msg, "parts", None) or [])
            if part.get("tool_call_id")
        }
        if tool_ids.intersection(first_tool_ids):
            return msg
    return None


def _tool_call_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        part for part in parts
        if isinstance(part, dict)
        and part.get("type") == "tool_call"
        and part.get("tool_call_id")
        and part.get("projection_state") != "discarded"
    ]


def _tool_result_ids(parts: list[dict[str, Any]]) -> list[str]:
    return [
        str(part.get("tool_call_id"))
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "tool_result"
        and part.get("tool_call_id")
        and part.get("projection_state") != "discarded"
    ]


def _message_seq_slice(messages: list[Any], start: int, stop: int) -> list[int]:
    return [
        int(getattr(message, "seq", idx))
        for idx, message in enumerate(messages[start:stop], start=start)
    ]


def _checkpoint_has_active_next(state: CheckpointState) -> bool:
    return any(
        node
        and str(node) not in {"__end__", "end", "END"}
        for node in state.next_nodes
    )


def _pending_permission_tool_call_ids(pending_permissions: list[Any]) -> set[str]:
    ids: set[str] = set()
    for permission in pending_permissions or []:
        tool_call_id = getattr(permission, "tool_call_id", None)
        if tool_call_id:
            ids.add(str(tool_call_id))
    return ids


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _unique_int(values: list[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
