"""v0.4.4 Phase B recovery policy tests."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_state import CheckpointStateKind, classify_checkpoint
from agent.recovery_policy import (
    RecoveryDecisionKind,
    RecoveryPolicy,
    RecoveryPolicyInput,
)


def _closed_tool_result_state():
    return classify_checkpoint(
        [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_1", name="bash"),
        ],
        next_nodes=["model"],
    )


def _hitl_state():
    return classify_checkpoint(
        [
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_2", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_2"]})],
    )


def _invalid_state():
    return classify_checkpoint([
        HumanMessage(content="run"),
        ToolMessage(content="orphan", tool_call_id="call_missing", name="bash"),
    ])


def _input(**overrides):
    data = {
        "session_status": "error",
        "failed_run_status": "failed",
        "failed_run_error": "ReadTimeout: provider timed out",
        "checkpoint_state": _closed_tool_result_state(),
        "source_run_id": "run-1",
    }
    data.update(overrides)
    return RecoveryPolicyInput(**data)


def test_provider_timeout_after_tool_result_allows_continue():
    decision = RecoveryPolicy.decide(_input())

    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True
    assert decision.retry_kind == "model_continuation"
    assert decision.target_run_type == "continue"
    assert decision.target_payload == {
        "mode": "retry_model_node",
        "source_run_id": "run-1",
    }
    assert decision.checkpoint_state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT.value


def test_connection_error_after_tool_result_allows_continue():
    decision = RecoveryPolicy.decide(_input(
        failed_run_error="ConnectError: connection failed",
    ))

    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True
    assert decision.provider_error_category == "provider_connection_error"


def test_provider_protocol_error_does_not_continue():
    decision = RecoveryPolicy.decide(_input(
        failed_run_error="HTTPStatusError: 400 Bad Request",
    ))

    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert decision.allowed is False
    assert "provider_error_not_continuable" in decision.reason


def test_provider_payload_validation_error_does_not_continue():
    decision = RecoveryPolicy.decide(_input(
        failed_run_error="PROVIDER_PAYLOAD_VALIDATION_ERROR: bad payload",
    ))

    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert decision.provider_error_category == "provider_payload_validation_error"


def test_transcript_integrity_error_does_not_continue():
    decision = RecoveryPolicy.decide(_input(
        failed_run_error="TranscriptIntegrityError: orphan tool result",
    ))

    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert decision.provider_error_category == "transcript_integrity_error"


def test_projection_repair_recoverable_allows_continue():
    decision = RecoveryPolicy.decide(_input(
        failed_run_error="RuntimeIntegrityError: db_tail_open_tool_call",
        diagnostics={"projection_repair_recoverable": True},
    ))

    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True
    assert decision.reason == "db_projection_ahead_repaired_after_closed_tool_result"
    assert decision.provider_error_category == "runtime_projection_repaired"


def test_invalid_live_checkpoint_overrides_recoverable_diagnostics():
    decision = RecoveryPolicy.decide(_input(
        diagnostics={"recoverable_model_continuation": True},
        checkpoint_state=_invalid_state(),
    ))

    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert "checkpoint_not_continuable" in decision.reason


def test_hitl_pending_maps_to_waiting_permission():
    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="error",
        checkpoint_state=_hitl_state(),
        hitl_permission_state="pending",
    ))

    assert decision.kind == RecoveryDecisionKind.WAITING_PERMISSION
    assert decision.allowed is False
    assert decision.target_session_status == "waiting"


def test_hitl_resolved_maps_to_resume():
    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="error",
        checkpoint_state=_hitl_state(),
        hitl_permission_state="approved",
    ))

    assert decision.kind == RecoveryDecisionKind.RESUME_OPEN_HITL
    assert decision.allowed is True
    assert decision.target_run_type == "resume"


def test_active_session_does_not_continue_model_retry():
    decision = RecoveryPolicy.decide(_input(session_status="running"))

    assert decision.kind == RecoveryDecisionKind.NONE
    assert decision.allowed is False


def test_waiting_session_does_not_continue_model_retry():
    decision = RecoveryPolicy.decide(_input(session_status="waiting"))

    assert decision.kind == RecoveryDecisionKind.NONE
    assert decision.allowed is False


def test_missing_diagnostics_but_live_checkpoint_allows_continue():
    decision = RecoveryPolicy.decide(_input(diagnostics=None))

    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL
    assert decision.allowed is True


def test_missing_source_run_id_blocks_continue_payload():
    decision = RecoveryPolicy.decide(_input(source_run_id=None))

    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
    assert decision.reason == "missing_source_run_id"
