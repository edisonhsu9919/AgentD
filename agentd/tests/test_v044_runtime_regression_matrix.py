"""v0.4.4 Phase E runtime core regression matrix tests."""

from __future__ import annotations

import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_state import CheckpointStateKind, classify_checkpoint_snapshot
from agent.diagnostics import build_checkpoint_diagnostics
from agent.hitl_runtime import HITLRuntime
from agent.message_persistence import build_persistable_message_parts
from agent.provider_payload_validator import validate_provider_payload
from agent.recovery_policy import RecoveryDecisionKind, RecoveryPolicy, RecoveryPolicyInput
from tests.helpers.mock_provider import (
    closed_tool_result_snapshot,
    invalid_provider_payload_messages,
    open_hitl_snapshot,
    read_timeout,
    valid_provider_payload_after_tool_result,
)
from tools.registry import ToolLoopCircuitBreaker, get_tool_loop_guard_diagnostics, reset_tool_call_counter


RUNTIME_REGRESSION_MATRIX = [
    {
        "id": "E-01",
        "risk_area": "checkpoint_classifier",
        "scenario": "closed tool_result checkpoint routes to model continuation",
        "coverage": "unit",
        "test": "test_matrix_checkpoint_and_recovery_invariants",
        "required": True,
    },
    {
        "id": "E-02",
        "risk_area": "diagnostics_recorder",
        "scenario": "provider timeout diagnostics preserve retry compatibility fields",
        "coverage": "unit",
        "test": "test_matrix_checkpoint_and_recovery_invariants",
        "required": True,
    },
    {
        "id": "E-03",
        "risk_area": "recovery_policy",
        "scenario": "only closed tool_result provider failures become continue runs",
        "coverage": "unit",
        "test": "test_matrix_checkpoint_and_recovery_invariants",
        "required": True,
    },
    {
        "id": "E-04",
        "risk_area": "provider_payload_validator",
        "scenario": "strict provider payload validation catches open tool calls",
        "coverage": "unit",
        "test": "test_matrix_provider_payload_validator_boundaries",
        "required": True,
    },
    {
        "id": "E-05",
        "risk_area": "hitl_provider_boundary",
        "scenario": "open HITL interrupt is resumable as HITL but not provider-ready",
        "coverage": "unit",
        "test": "test_matrix_hitl_boundary_is_not_provider_continuation",
        "required": True,
    },
    {
        "id": "E-06",
        "risk_area": "message_persistence",
        "scenario": "visible reasoning and tool calls remain split into persisted parts",
        "coverage": "unit",
        "test": "test_matrix_message_persistence_preserves_user_visible_parts",
        "required": True,
    },
    {
        "id": "E-07",
        "risk_area": "tool_loop_breaker",
        "scenario": "hard stop diagnostics carry canonical args",
        "coverage": "unit",
        "test": "test_matrix_tool_loop_breaker_diagnostics_contract",
        "required": True,
    },
    {
        "id": "E-08",
        "risk_area": "subagent_bridge",
        "scenario": "phase 7C workspace inheritance remains covered by dedicated test",
        "coverage": "targeted",
        "test": "tests/test_phase_7c_subagent_workspace.py",
        "required": True,
    },
    {
        "id": "E-09",
        "risk_area": "microcompact_strict_provider",
        "scenario": "strict provider safety remains covered by Phase P4B regression",
        "coverage": "targeted",
        "test": "tests/test_phase_p4b_microcompact.py",
        "required": True,
    },
    {
        "id": "E-10",
        "risk_area": "executor_split",
        "scenario": "start/resume/continue delegation remains covered by split tests",
        "coverage": "targeted",
        "test": "tests/test_v044_executor_split.py + tests/test_v044_continue_run.py",
        "required": True,
    },
]


def test_runtime_regression_matrix_manifest_covers_required_risk_areas():
    expected = {
        "checkpoint_classifier",
        "diagnostics_recorder",
        "recovery_policy",
        "provider_payload_validator",
        "hitl_provider_boundary",
        "message_persistence",
        "tool_loop_breaker",
        "subagent_bridge",
        "microcompact_strict_provider",
        "executor_split",
    }

    assert {row["risk_area"] for row in RUNTIME_REGRESSION_MATRIX} == expected
    assert all(row["required"] for row in RUNTIME_REGRESSION_MATRIX)
    assert all(row["id"].startswith("E-") for row in RUNTIME_REGRESSION_MATRIX)


def test_matrix_checkpoint_and_recovery_invariants():
    snapshot = closed_tool_result_snapshot()
    state = classify_checkpoint_snapshot(snapshot)
    exc = read_timeout()
    diagnostics = build_checkpoint_diagnostics(snapshot=snapshot, exception=exc)

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error=str(exc),
        diagnostics=diagnostics,
        checkpoint_state=state,
        source_run_id="run-1",
    ))

    assert state.state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT
    assert state.is_provider_payload_ready is True
    assert diagnostics["checkpoint_valid"] is True
    assert diagnostics["provider_error_category"] == "provider_timeout"
    assert diagnostics["retry_kind"] == "model_continuation"
    assert decision.kind == RecoveryDecisionKind.CONTINUE_MODEL


def test_matrix_provider_payload_validator_boundaries():
    invalid = validate_provider_payload(
        invalid_provider_payload_messages(),
        provider_family="deepseek",
    )
    valid = validate_provider_payload(
        valid_provider_payload_after_tool_result(),
        provider_family="deepseek",
    )

    assert invalid.ok is False
    assert invalid.issues[0].code == "assistant_tool_call_missing_tool_result"
    assert valid.ok is True


def test_matrix_hitl_boundary_is_not_provider_continuation():
    snapshot = open_hitl_snapshot()
    state = classify_checkpoint_snapshot(snapshot)

    pending = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error="permission pending",
        checkpoint_state=state,
        hitl_permission_state="pending",
        source_run_id="run-hitl",
    ))
    resolved = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error="permission approved",
        checkpoint_state=state,
        hitl_permission_state="approved",
        source_run_id="run-hitl",
    ))

    assert state.state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL
    assert state.requires_human_input is True
    assert state.is_provider_payload_ready is False
    assert HITLRuntime.snapshot_is_open_interrupt(snapshot) is True
    assert pending.kind == RecoveryDecisionKind.WAITING_PERMISSION
    assert resolved.kind == RecoveryDecisionKind.RESUME_OPEN_HITL


def test_matrix_message_persistence_preserves_user_visible_parts():
    message = AIMessage(
        content="<think>trace</think>Final answer",
        id="ai-runtime-1",
        tool_calls=[{"id": "call_1", "name": "bash", "args": {"command": "ls"}}],
    )

    role, parts, is_summary = build_persistable_message_parts(message)

    assert role == "assistant"
    assert is_summary is False
    assert [part["type"] for part in parts] == ["reasoning", "text", "tool_call"]
    assert parts[2]["runtime_message_id"] == "ai-runtime-1"


def test_matrix_tool_loop_breaker_diagnostics_contract():
    session_id = str(uuid.uuid4())
    reset_tool_call_counter(session_id)
    breaker = ToolLoopCircuitBreaker(
        session_id=session_id,
        tool_name="skill",
        canonical_args={"action": "load"},
        blocked_count=4,
        identical_call_count=7,
        reason="identical_tool_call_loop",
        message="loop stopped",
    )

    assert breaker.canonical_args == {"action": "load"}
    assert breaker.identical_call_count == 7

    diagnostics = get_tool_loop_guard_diagnostics(session_id)
    assert diagnostics["tool_loop_guard_triggered"] is False
    assert diagnostics["tool_loop_total_calls"] == 0


def test_matrix_checkpoint_invalid_orphan_tool_message_stays_hard_error():
    snapshot = closed_tool_result_snapshot()
    snapshot.values["messages"] = [
        HumanMessage(content="run"),
        ToolMessage(content="orphan", tool_call_id="missing", name="bash"),
    ]
    state = classify_checkpoint_snapshot(snapshot)
    diagnostics = build_checkpoint_diagnostics(
        snapshot=snapshot,
        exception=read_timeout(),
    )

    decision = RecoveryPolicy.decide(RecoveryPolicyInput(
        session_status="idle",
        failed_run_status="failed",
        failed_run_error="ReadTimeout",
        diagnostics=diagnostics,
        checkpoint_state=state,
        source_run_id="run-invalid",
    ))

    assert state.state_kind == CheckpointStateKind.INVALID_ORPHAN_TOOL_MESSAGE
    assert diagnostics["recoverable_model_continuation"] is False
    assert decision.kind == RecoveryDecisionKind.HARD_ERROR
