"""v0.4.4 Phase E mock provider failure regression tests."""

from __future__ import annotations

from agent.checkpoint_state import classify_checkpoint_snapshot
from agent.diagnostics import build_checkpoint_diagnostics, classify_provider_error
from agent.provider_payload_validator import (
    ProviderPayloadValidationError,
    assert_provider_payload_valid,
)
from agent.recovery_policy import (
    RecoveryDecisionKind,
    RecoveryPolicy,
    RecoveryPolicyInput,
)
from tests.helpers.mock_provider import (
    closed_tool_result_snapshot,
    connect_error,
    invalid_provider_payload_messages,
    protocol_400_error,
    read_timeout,
)


def test_timeout_after_tool_result_is_continuable():
    snapshot = closed_tool_result_snapshot()
    state = classify_checkpoint_snapshot(snapshot)
    exc = read_timeout()
    diagnostics = build_checkpoint_diagnostics(
        snapshot=snapshot,
        exception=exc,
    )

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error=str(exc),
        diagnostics=diagnostics,
        checkpoint_state=state,
        source_run_id="run-timeout",
    ))

    assert classify_provider_error(exc).value == "provider_timeout"
    assert diagnostics["recoverable_model_continuation"] is True
    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True
    assert decision.target_payload == {
        "mode": "retry_model_node",
        "source_run_id": "run-timeout",
    }


def test_connection_error_after_tool_result_is_continuable_by_policy():
    snapshot = closed_tool_result_snapshot()
    state = classify_checkpoint_snapshot(snapshot)
    exc = connect_error()
    diagnostics = build_checkpoint_diagnostics(
        snapshot=snapshot,
        exception=exc,
    )

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error=str(exc),
        diagnostics=diagnostics,
        checkpoint_state=state,
        source_run_id="run-connect",
    ))

    assert diagnostics["provider_error_category"] == "provider_connection_error"
    assert diagnostics["recoverable_model_continuation"] is False
    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True


def test_protocol_400_after_tool_result_is_hard_error():
    snapshot = closed_tool_result_snapshot()
    state = classify_checkpoint_snapshot(snapshot)
    exc = protocol_400_error()
    diagnostics = build_checkpoint_diagnostics(
        snapshot=snapshot,
        exception=exc,
    )

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error=str(exc),
        diagnostics=diagnostics,
        checkpoint_state=state,
        source_run_id="run-400",
    ))

    assert diagnostics["provider_error_category"] == "provider_protocol_error"
    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert decision.allowed is False


def test_provider_payload_validation_failure_is_hard_error():
    try:
        assert_provider_payload_valid(
            invalid_provider_payload_messages(),
            provider_family="deepseek",
        )
    except ProviderPayloadValidationError as exc:
        diagnostics = build_checkpoint_diagnostics(
            messages=[],
            exception=exc,
        )
    else:  # pragma: no cover - the assertion above must fail
        raise AssertionError("invalid provider payload unexpectedly passed")

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error=diagnostics["exception_message"],
        diagnostics=diagnostics,
        checkpoint_state=classify_checkpoint_snapshot(closed_tool_result_snapshot()),
        source_run_id="run-payload",
    ))

    assert diagnostics["provider_error_category"] == "provider_payload_validation_error"
    assert diagnostics["provider_payload_validation_error"] is True
    assert diagnostics["provider_family"] == "deepseek"
    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
