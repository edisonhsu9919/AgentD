"""v0.4.9 Phase A: runtime truth boundary tests.

These tests pin down the new runtime contract:
- Checkpoint state is the runtime truth source.
- DB tail anomalies are diagnostics-only and do not produce FAIL_INTEGRITY_ERROR.
- The legacy DB-tail-as-truth behavior is preserved behind the rollback flag.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_state import classify_checkpoint
from agent.runtime_error_classifier import RuntimeErrorClassifier
from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeIntegrityGate,
    RuntimeIntegrityInput,
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


def test_db_tail_open_tool_call_alone_is_diagnostics_only():
    """No checkpoint + dirty DB tail → FINALIZE_IDLE with diagnostics."""
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_a", "bash")])],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True
    # Diagnostics still surface the open tool_call ids.
    assert decision.open_tool_call_ids == ["call_a"]
    assert decision.db_tail_seq == 60


def test_orphan_tool_result_alone_is_diagnostics_only():
    """No checkpoint + DB tail with orphan tool_result → FINALIZE_IDLE."""
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [_db_message(20, "tool", [_tool_result_part("call_x", "bash")])],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True


def test_window_truncation_does_not_kill_session():
    """fe81beb6-style regression: assistant tool_call sliced out by window.

    Historical bug: 20-message DB tail window started at the tool_result,
    making the orphan tool_result look fatal even though business completed.
    Under v0.4.9 default, the gate must not fail on this.
    """
    # Simulated tail: tool_result without the preceding assistant tool_call.
    # (In practice, the layered validator would expand the boundary; this test
    # asserts that even without expansion, the gate stays soft.)
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [
            _db_message(146, "tool", [_tool_result_part("call_lost", "bash")]),
            _db_message(147, "assistant", [{"type": "text", "content": "ok"}]),
        ],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True


def test_clean_checkpoint_with_dirty_db_tail_finalizes_idle():
    """Provider-ready checkpoint + dirty DB projection → still idle."""
    checkpoint = classify_checkpoint(
        messages=[HumanMessage(content="hi"), AIMessage(content="done")],
        next_nodes=[],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [
            _db_message(1, "assistant", [_tool_call_part("call_orphan", "bash")]),
        ],
    ))

    assert decision.action == RuntimeGateAction.FINALIZE_IDLE
    assert decision.can_accept_user_prompt is True


def test_invalid_checkpoint_still_terminal():
    """Real fail: when LangGraph state itself is invalid, gate must fail."""
    # classify_checkpoint with an orphan tool_call (no matching tool message)
    # produces an INVALID_ORPHAN_TOOL_CALL state.
    checkpoint = classify_checkpoint(
        messages=[
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_x", "name": "bash", "args": {}}],
            ),
        ],
        next_nodes=["model"],
    )
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        checkpoint,
        [],
    ))

    # The checkpoint is recognized as invalid (orphan tool_call). Gate must fail.
    assert decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
    assert decision.reason.startswith("checkpoint_invalid")


def test_checkpoint_projection_mismatch_is_recoverable_now():
    """v0.4.9 Phase A: projection mismatch errors no longer kill the session."""
    envelope = RuntimeErrorClassifier.classify_error_text(
        "RuntimeIntegrityError: stale projection mismatch"
    )

    assert envelope.category == "checkpoint_projection_mismatch"
    assert envelope.severity == "recoverable"
    assert envelope.safe_to_continue_user_prompt is True


def test_db_tail_dirty_under_legacy_flag_still_fails(monkeypatch):
    """Legacy v0.4.4 behavior remains available behind the rollback flag."""
    from core.config import settings as _settings

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)
    decision = RuntimeIntegrityGate.decide_terminal(_payload(
        None,
        [_db_message(60, "assistant", [_tool_call_part("call_a", "bash")])],
    ))

    assert decision.action == RuntimeGateAction.FAIL_INTEGRITY_ERROR
    assert decision.reason.startswith("db_tail_open_tool_call")


# ---------------------------------------------------------------------------
# v0.4.9 audit Finding 2: RuntimeIntegrityError classification is reason-aware.
# ---------------------------------------------------------------------------


def test_runtime_integrity_error_with_db_tail_reason_is_recoverable():
    """db_tail_open_tool_call: real DB projection mismatch → recoverable."""
    envelope = RuntimeErrorClassifier.classify_error_text(
        "RuntimeIntegrityError: db_tail_open_tool_call:assistant_tool_call_missing_tool_result"
    )

    # The legacy text-match path treats db_tail_open_tool_call as
    # provider_protocol_tool_adjacency for backward compat. Either bucket is
    # acceptable; both are recoverable.
    assert envelope.severity == "recoverable"
    assert envelope.category in {
        "provider_protocol_tool_adjacency",
        "checkpoint_projection_mismatch",
    }
    assert envelope.safe_to_continue_user_prompt is True


def test_runtime_integrity_error_with_checkpoint_invalid_is_terminal_corruption():
    """checkpoint_invalid:* reason → terminal checkpoint_corruption category."""
    envelope = RuntimeErrorClassifier.classify_error_text(
        "RuntimeIntegrityError: checkpoint_invalid:invalid_orphan_tool_call"
    )

    assert envelope.category == "checkpoint_corruption"
    assert envelope.severity == "terminal"
    assert envelope.safe_to_continue_user_prompt is False
    assert envelope.next_action == "admin_fix_config"


def test_runtime_integrity_error_classify_exception_uses_decision_reason():
    """classify_exception lifts decision.reason into context for reason-aware match."""
    from agent.runtime_integrity import (
        RuntimeGateAction,
        RuntimeGateDecision,
        RuntimeIntegrityError,
    )

    decision = RuntimeGateDecision(
        action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
        reason="checkpoint_invalid:invalid_orphan_tool_message",
    )
    exc = RuntimeIntegrityError(str(uuid.uuid4()), decision)
    envelope = RuntimeErrorClassifier.classify_exception(exc)

    assert envelope.category == "checkpoint_corruption"
    assert envelope.severity == "terminal"
    # Diagnostics carry the structured reason so doctor can show it.
    assert envelope.diagnostics.get("integrity_gate_reason") == \
        "checkpoint_invalid:invalid_orphan_tool_message"


def test_runtime_integrity_error_classify_exception_db_tail_is_recoverable():
    """db_tail_open_tool_call structured reason → recoverable, not corruption."""
    from agent.runtime_integrity import (
        RuntimeGateAction,
        RuntimeGateDecision,
        RuntimeIntegrityError,
    )

    decision = RuntimeGateDecision(
        action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
        reason="db_tail_open_tool_call:assistant_tool_call_missing_tool_result",
    )
    exc = RuntimeIntegrityError(str(uuid.uuid4()), decision)
    envelope = RuntimeErrorClassifier.classify_exception(exc)

    assert envelope.severity == "recoverable"
    assert envelope.category != "checkpoint_corruption"


def test_runtime_integrity_error_unsupported_state_is_recoverable_projection():
    """unsupported_checkpoint_state:* → recoverable projection mismatch, not corruption."""
    from agent.runtime_integrity import (
        RuntimeGateAction,
        RuntimeGateDecision,
        RuntimeIntegrityError,
    )

    decision = RuntimeGateDecision(
        action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
        reason="unsupported_checkpoint_state:invalid_unknown",
    )
    exc = RuntimeIntegrityError(str(uuid.uuid4()), decision)
    envelope = RuntimeErrorClassifier.classify_exception(exc)

    assert envelope.severity == "recoverable"
    assert envelope.category == "checkpoint_projection_mismatch"
