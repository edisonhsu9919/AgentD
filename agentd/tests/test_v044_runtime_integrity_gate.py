"""v0.4.4 follow-up RuntimeIntegrityGate tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_state import CheckpointStateKind, classify_checkpoint
from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeIntegrityGate,
    RuntimeIntegrityInput,
    build_validation_message_slice,
    inspect_db_transcript_tail,
)


def _db_message(seq: int, role: str, parts: list[dict]):
    return SimpleNamespace(seq=seq, role=role, parts=parts)


def _tool_call_part(tool_call_id: str, name: str = "bash"):
    return {
        "type": "tool_call",
        "tool_call_id": tool_call_id,
        "tool_name": name,
        "input": {},
    }


def _tool_result_part(tool_call_id: str, name: str = "bash"):
    return {
        "type": "tool_result",
        "tool_call_id": tool_call_id,
        "tool_name": name,
        "output": "ok",
    }


def _payload(checkpoint_state, db_messages, *, status="idle", pending=(), error=None):
    return RuntimeIntegrityInput(
        session_id=str(uuid.uuid4()),
        session_status=status,
        checkpoint_state=checkpoint_state,
        db_tail_messages=list(db_messages),
        pending_permissions=list(pending),
        latest_error=error,
    )


def test_db_tail_detects_open_tool_call_without_result():
    tail = inspect_db_transcript_tail([
        _db_message(1, "user", [{"type": "text", "content": "go"}]),
        _db_message(2, "assistant", [_tool_call_part("call_1", "file_edit")]),
    ])

    assert tail.has_open_tool_call is True
    assert tail.open_tool_call_ids == ["call_1"]
    assert tail.reason == "assistant_tool_call_missing_tool_result"


def test_db_tail_ignores_discarded_projection_tool_call():
    part = _tool_call_part("call_1", "file_edit")
    part["projection_state"] = "discarded"
    tail = inspect_db_transcript_tail([
        _db_message(1, "user", [{"type": "text", "content": "go"}]),
        _db_message(2, "assistant", [part]),
    ])

    assert tail.clean is True
    assert tail.open_tool_call_ids == []


def test_db_tail_detects_user_inserted_between_tool_group():
    tail = inspect_db_transcript_tail([
        _db_message(1, "assistant", [_tool_call_part("call_1", "file_edit")]),
        _db_message(2, "user", [{"type": "text", "content": "new prompt"}]),
        _db_message(3, "tool", [_tool_result_part("call_1", "file_edit")]),
    ])

    assert tail.invalid_user_inserted_between_tool_group is True
    assert tail.open_tool_call_ids == ["call_1"]


def test_db_tail_detects_partial_mixed_tool_group():
    tail = inspect_db_transcript_tail([
        _db_message(1, "assistant", [
            _tool_call_part("call_bash", "bash"),
            _tool_call_part("call_todo", "todo_update"),
        ]),
        _db_message(2, "tool", [_tool_result_part("call_bash", "bash")]),
    ])

    assert tail.has_open_tool_call is True
    assert tail.open_tool_call_ids == ["call_todo"]
    assert tail.reason == "partial_tool_group_closed"


def test_terminal_gate_finalizes_idle_when_db_tail_open_but_checkpoint_absent():
    """v0.4.9 Phase A: DB tail open tool_call alone no longer kills the session.

    Without a checkpoint, the gate falls through to FINALIZE_IDLE; DB tail
    diagnostics are still attached to the decision for visibility.
    """
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_edit", "file_edit")])],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True
    # Diagnostics still surface the open tool_call for doctor / observability.
    assert decision.open_tool_call_ids == ["call_edit"]
    assert decision.db_tail_seq == 60


def test_terminal_gate_legacy_db_tail_failure_under_flag(monkeypatch):
    """Legacy v0.4.4 behavior remains available behind the rollback flag."""
    from core.config import settings as _settings

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_edit", "file_edit")])],
    ))

    assert decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
    assert decision.reason.startswith("db_tail_open_tool_call")
    assert decision.can_accept_user_prompt is False


def test_hitl_open_with_pending_permission_enters_waiting():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_write", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_write"]})],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [_db_message(2, "assistant", [_tool_call_part("call_write", "file_write")])],
        pending=[SimpleNamespace(tool_call_id="call_write", status="pending")],
    ))

    assert decision.action == RuntimeGateAction.ENTER_WAITING
    assert decision.requires_human_input is True
    assert decision.open_tool_call_ids == ["call_write"]


def test_hitl_open_without_permission_fails_instead_of_idle():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_write", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_write"]})],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [_db_message(2, "assistant", [_tool_call_part("call_write", "file_write")])],
    ))

    assert decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
    assert decision.reason == "hitl_open_tool_call_missing_pending_permission"


def test_mixed_parallel_hitl_partial_group_with_matching_permission_enters_waiting():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_list", "name": "list_dir", "args": {"path": "."}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_list", name="list_dir"),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "assistant", [
                _tool_call_part("call_list", "list_dir"),
                _tool_call_part("call_bash", "bash"),
            ]),
            _db_message(2, "tool", [_tool_result_part("call_list", "list_dir")]),
        ],
        pending=[SimpleNamespace(tool_call_id="call_bash", status="pending")],
    ))

    assert checkpoint.state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL
    assert decision.action == RuntimeGateAction.ENTER_WAITING
    assert decision.reason == "hitl_open_tool_call_waiting"
    assert decision.checkpoint_state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL.value
    assert decision.requires_human_input is True
    assert decision.open_tool_call_ids == ["call_bash"]


def test_mixed_parallel_hitl_partial_group_with_unmatched_permission_fails():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_list", "name": "list_dir", "args": {"path": "."}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_list", name="list_dir"),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "assistant", [
                _tool_call_part("call_list", "list_dir"),
                _tool_call_part("call_bash", "bash"),
            ]),
            _db_message(2, "tool", [_tool_result_part("call_list", "list_dir")]),
        ],
        pending=[SimpleNamespace(tool_call_id="call_other", status="pending")],
    ))

    assert decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
    # v0.4.9 Phase A: same scenario, but the failure now comes from the
    # pending-permission branch instead of the DB-tail open-tool-call branch
    # (DB tail is diagnostics-only). Both reasons describe HITL/permission mismatch.
    assert decision.reason in {
        "hitl_open_tool_call_missing_pending_permission",
        "pending_permission_without_matching_open_hitl_checkpoint",
    }


def test_invalid_orphan_partial_group_with_matching_permission_enters_waiting():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_list", "name": "list_dir", "args": {"path": "."}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_list", name="list_dir"),
        ],
        next_nodes=["model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "assistant", [
                _tool_call_part("call_list", "list_dir"),
                _tool_call_part("call_bash", "bash"),
            ]),
            _db_message(2, "tool", [_tool_result_part("call_list", "list_dir")]),
        ],
        pending=[SimpleNamespace(tool_call_id="call_bash", status="pending")],
    ))

    assert checkpoint.state_kind == CheckpointStateKind.INVALID_ORPHAN_TOOL_CALL
    assert decision.action == RuntimeGateAction.ENTER_WAITING
    assert decision.reason == "hitl_open_tool_call_waiting"
    assert decision.checkpoint_state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL.value
    assert decision.open_tool_call_ids == ["call_bash"]


def test_next_model_after_tool_result_cannot_finalize_idle():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_1", name="bash"),
        ],
        next_nodes=["model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "assistant", [_tool_call_part("call_1")]),
            _db_message(2, "tool", [_tool_result_part("call_1")]),
        ],
    ))

    assert decision.action == RuntimeGateAction.CONTINUE_MODEL
    assert decision.reason == "checkpoint_next_model_after_tool_result"


def test_clean_checkpoint_and_db_tail_can_finalize_idle():
    checkpoint = classify_checkpoint(
        messages=[HumanMessage(content="hi"), AIMessage(content="done")],
        next_nodes=[],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "user", [{"type": "text", "content": "hi"}]),
            _db_message(2, "assistant", [{"type": "text", "content": "done"}]),
        ],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True


def test_provider_ready_with_model_next_and_clean_db_tail_can_finalize_idle():
    checkpoint = classify_checkpoint(
        messages=[HumanMessage(content="hi"), AIMessage(content="done")],
        next_nodes=["model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "user", [{"type": "text", "content": "hi"}]),
            _db_message(2, "assistant", [{"type": "text", "content": "done"}]),
        ],
    ))

    assert checkpoint.state_kind.value == "provider_ready"
    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    # v0.4.9: reason renamed; clean DB tail now produces "checkpoint_clean".
    assert decision.reason == "checkpoint_clean"


def test_prompt_ingress_accepts_when_db_tail_dirty_and_checkpoint_absent():
    """v0.4.9 Phase A: dirty DB tail without checkpoint no longer blocks ingress."""
    decision = RuntimeIntegrityGate.decide_prompt_ingress(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_edit", "file_edit")])],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True


def test_prompt_ingress_legacy_rejects_open_runtime_state_under_flag(monkeypatch):
    """Legacy v0.4.4 behavior remains available behind the rollback flag."""
    from core.config import settings as _settings

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)
    decision = RuntimeIntegrityGate.decide_prompt_ingress(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_edit", "file_edit")])],
    ))

    assert decision.action == RuntimeGateAction.REJECT_NEW_PROMPT
    assert decision.can_accept_user_prompt is False


def test_prompt_ingress_allows_clean_checkpoint_with_db_only_dirty_tail():
    checkpoint = classify_checkpoint(
        messages=[HumanMessage(content="hi"), AIMessage(content="done")],
        next_nodes=[],
    )

    decision = RuntimeIntegrityGate.decide_prompt_ingress(_payload(
        checkpoint,
        [_db_message(60, "assistant", [_tool_call_part("call_edit", "file_edit")])],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.reason == "checkpoint_clean_prompt_ingress"
    assert decision.can_accept_user_prompt is True
    assert decision.open_tool_call_ids == []


def test_prompt_ingress_still_rejects_checkpoint_open_hitl():
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_write", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_write"]})],
    )

    decision = RuntimeIntegrityGate.decide_prompt_ingress(_payload(
        checkpoint,
        [],
        pending=[SimpleNamespace(tool_call_id="call_write", status="pending")],
    ))

    assert decision.action == RuntimeGateAction.REJECT_NEW_PROMPT
    assert decision.requires_human_input is True
    assert decision.open_tool_call_ids == ["call_write"]


def test_subtask_waiting_is_not_final_idle():
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [],
        status="subtask_waiting",
    ))

    assert decision.action == RuntimeGateAction.ENTER_SUBTASK_WAITING
    assert decision.can_accept_user_prompt is False


def test_terminal_validation_boundary_expands_truncated_tool_result():
    messages = [
        _db_message(145, "assistant", [_tool_call_part("call_a")]),
        _db_message(146, "tool", [_tool_result_part("call_a")]),
        *[
            _db_message(seq, "assistant", [{"type": "text", "content": "ok"}])
            for seq in range(147, 166)
        ],
    ]

    legacy_tail = inspect_db_transcript_tail(messages[-20:])
    validation = build_validation_message_slice(messages, run_start_seq=None)
    tail = inspect_db_transcript_tail(validation.messages)

    assert legacy_tail.reason == "orphan_tool_message"
    assert validation.scope == "boundary_expanded"
    assert validation.expanded_from_seq == 145
    assert tail.clean is True


def test_run_slice_catches_early_unclosed_tool_call_outside_recent_tail():
    messages = [
        _db_message(100, "assistant", [_tool_call_part("call_early")]),
        *[
            _db_message(seq, "assistant", [{"type": "text", "content": "later"}])
            for seq in range(101, 131)
        ],
    ]

    validation = build_validation_message_slice(messages, run_start_seq=99)
    tail = inspect_db_transcript_tail(validation.messages)

    assert validation.scope == "run_slice"
    assert tail.has_open_tool_call is True
    assert tail.open_tool_call_ids == ["call_early"]


def test_boundary_expansion_keeps_multi_tool_group_complete():
    messages = [
        _db_message(10, "assistant", [
            _tool_call_part("call_a"),
            _tool_call_part("call_b", "todo_update"),
        ]),
        _db_message(11, "tool", [_tool_result_part("call_a")]),
        _db_message(12, "tool", [_tool_result_part("call_b", "todo_update")]),
        _db_message(13, "assistant", [{"type": "text", "content": "done"}]),
    ]

    validation = build_validation_message_slice(messages, run_start_seq=11)
    tail = inspect_db_transcript_tail(validation.messages)

    assert validation.scope == "boundary_expanded"
    assert validation.expanded_from_seq == 10
    assert tail.clean is True


def test_boundary_expansion_detects_partial_multi_tool_group():
    messages = [
        _db_message(10, "assistant", [
            _tool_call_part("call_a"),
            _tool_call_part("call_b", "todo_update"),
        ]),
        _db_message(11, "tool", [_tool_result_part("call_a")]),
    ]

    validation = build_validation_message_slice(messages, run_start_seq=10)
    tail = inspect_db_transcript_tail(validation.messages)

    assert validation.scope == "boundary_expanded"
    assert tail.has_open_tool_call is True
    assert tail.open_tool_call_ids == ["call_b"]


def test_terminal_decision_records_validation_scope():
    checkpoint = classify_checkpoint(
        messages=[HumanMessage(content="hi"), AIMessage(content="done")],
        next_nodes=[],
    )
    decision = RuntimeIntegrityGate.decide_terminal(RuntimeIntegrityInput(
        session_id=str(uuid.uuid4()),
        session_status="idle",
        checkpoint_state=checkpoint,
        db_tail_messages=[
            _db_message(1, "user", [{"type": "text", "content": "hi"}]),
            _db_message(2, "assistant", [{"type": "text", "content": "done"}]),
        ],
        validation_scope="run_slice",
        run_start_seq=1,
        run_end_seq=2,
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.validation_scope == "run_slice"
    assert decision.to_dict()["run_start_seq"] == 1
