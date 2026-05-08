"""v0.4.7 Phase A runtime recovery API contract tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from agent.runtime_error_classifier import RuntimeErrorClassifier, recovery_state_from_envelope
from session.schemas import RuntimeResponse
from session.router import _active_recovery_run, _runtime_recovery_flags


def test_runtime_response_exposes_v047_recovery_truth_fields():
    runtime = RuntimeResponse(
        session_id=uuid.uuid4(),
        status="idle",
        phase=None,
        last_message_seq=3,
        pending_permissions_count=0,
        resumable=False,
        updated_at=datetime.now(timezone.utc),
        recovery_state="recoverable",
        recovery_envelope={
            "category": "provider_transient",
            "severity": "recoverable",
            "source": "provider",
            "safe_to_retry": True,
            "safe_to_continue_user_prompt": True,
            "next_action": "retry",
            "user_message": "retry later",
            "developer_message": "APIConnectionError",
            "diagnostics": {},
            "auto_recovery": {},
        },
        last_run_error_category="provider_transient",
        next_action="retry",
        can_accept_user_prompt=True,
        can_retry=True,
        can_recover=False,
    )

    data = runtime.model_dump(mode="json")

    assert data["recovery_state"] == "recoverable"
    assert data["recovery_envelope"]["category"] == "provider_transient"
    assert data["last_run_error_category"] == "provider_transient"
    assert data["next_action"] == "retry"
    assert data["can_accept_user_prompt"] is True
    assert data["can_retry"] is True
    assert data["can_recover"] is False


def test_runtime_response_defaults_to_no_recovery():
    runtime = RuntimeResponse(
        session_id=uuid.uuid4(),
        status="idle",
        phase=None,
        last_message_seq=0,
        pending_permissions_count=0,
        resumable=False,
        updated_at=datetime.now(timezone.utc),
    )

    data = runtime.model_dump(mode="json")

    assert data["recovery_state"] == "none"
    assert data["recovery_envelope"] is None
    assert data["can_retry"] is False
    assert data["can_recover"] is False


def test_sse_recovery_event_names_are_documented_in_api_guide():
    from pathlib import Path

    content = Path(__file__).resolve().parents[2].joinpath("API_GUIDE.md").read_text(encoding="utf-8")

    assert "recovery_state_changed" in content
    assert "run_failed_recoverable" in content
    assert "run_failed_terminal" in content


def test_active_recovery_ignores_historical_failed_run_after_new_success():
    session = SimpleNamespace(status="idle")
    failed_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="failed",
        diagnostics={"recovery_envelope": {"category": "provider_transient"}},
    )
    completed_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        error=None,
        diagnostics={},
    )

    assert _active_recovery_run(session, completed_run, failed_run) is None


def test_active_recovery_accepts_latest_unresolved_session_envelope():
    session = SimpleNamespace(status="idle")
    latest_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        error=None,
        diagnostics={
            "recovery_unresolved": True,
            "recovery_envelope": {"category": "subtask_bridge_error"},
        },
    )

    assert _active_recovery_run(session, latest_run, None) is latest_run


def test_runtime_retry_flags_reject_config_errors_but_allow_context_recover():
    config_envelope = RuntimeErrorClassifier.classify_error_text(
        "model config not found",
    ).model_dump(mode="json")
    config_state = recovery_state_from_envelope(config_envelope)
    can_retry, can_recover, can_prompt = _runtime_recovery_flags(
        recovery_envelope=config_envelope,
        recovery_state=config_state,
        session_status="idle",
        can_accept_user_prompt=True,
    )

    assert can_retry is False
    assert can_recover is False
    assert can_prompt is True

    overflow_envelope = RuntimeErrorClassifier.classify_error_text(
        "maximum context length exceeded",
    ).model_dump(mode="json")
    overflow_state = recovery_state_from_envelope(overflow_envelope)
    can_retry, can_recover, can_prompt = _runtime_recovery_flags(
        recovery_envelope=overflow_envelope,
        recovery_state=overflow_state,
        session_status="idle",
        can_accept_user_prompt=True,
    )

    assert can_retry is False
    assert can_recover is True
    assert can_prompt is True
    assert overflow_envelope["auto_recovery"]["next_strategy"] == "reactive_compact_then_continue"


def test_runtime_flags_keep_checkpoint_dirty_prompt_gate_closed():
    envelope = RuntimeErrorClassifier.classify_error_text(
        "assistant message with 'tool_calls' must be followed by tool messages",
    ).model_dump(mode="json")
    state = recovery_state_from_envelope(envelope)

    can_retry, can_recover, can_prompt = _runtime_recovery_flags(
        recovery_envelope=envelope,
        recovery_state=state,
        session_status="idle",
        can_accept_user_prompt=False,
    )

    assert can_retry is True
    assert can_recover is True
    assert can_prompt is False
