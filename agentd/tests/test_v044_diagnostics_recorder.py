"""v0.4.4 Phase A diagnostics recorder tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.diagnostics import (
    DiagnosticsRecorder,
    ProviderErrorCategory,
    build_checkpoint_diagnostics,
    build_exception_diagnostics,
    capture_checkpoint_snapshot,
    classify_provider_error,
)
from agent.provider_reasoning import TranscriptIntegrityError


def _next_model_snapshot():
    messages = [
        HumanMessage(content="list"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_ls", "name": "bash", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id="call_ls", name="bash"),
    ]
    return SimpleNamespace(values={"messages": messages}, next=("model",), interrupts=[])


def _hitl_snapshot():
    messages = [
        HumanMessage(content="write"),
        AIMessage(
            content="",
            tool_calls=[{
                "id": "call_write",
                "name": "file_write",
                "args": {"path": "x.txt", "content": "x"},
            }],
        ),
    ]
    return SimpleNamespace(
        values={"messages": messages},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{
                "name": "file_write",
                "args": {"path": "x.txt", "content": "x"},
            }],
            "tool_call_ids": ["call_write"],
        })],
    )


def test_checkpoint_diagnostics_include_composition_and_state():
    snapshot = _next_model_snapshot()

    diagnostics = DiagnosticsRecorder.build_checkpoint_diagnostics(snapshot=snapshot)

    assert diagnostics["checkpoint_state_kind"] == "next_model_after_tool_result"
    assert diagnostics["checkpoint_state_reason"] == "all_tool_calls_closed_next_model"
    assert diagnostics["checkpoint_next"] == ["model"]
    assert diagnostics["checkpoint_message_count"] == 3
    assert diagnostics["checkpoint_composition"] == {
        "human": 1,
        "ai": 1,
        "tool": 1,
        "system": 0,
        "other": 0,
        "total": 3,
    }
    assert diagnostics["checkpoint_valid"] is True
    assert diagnostics["closed_tool_call_ids"] == ["call_ls"]


def test_hitl_waiting_diagnostics_require_snapshot_context():
    snapshot = _hitl_snapshot()
    messages = snapshot.values["messages"]

    without_snapshot = build_checkpoint_diagnostics(messages=messages)
    with_snapshot = build_checkpoint_diagnostics(messages=messages, snapshot=snapshot)

    assert without_snapshot["checkpoint_state_kind"] == "invalid_orphan_tool_call"
    assert with_snapshot["checkpoint_state_kind"] == "hitl_open_tool_call"
    assert with_snapshot["requires_human_input"] is True
    assert with_snapshot["is_provider_payload_ready"] is False
    assert with_snapshot["checkpoint_valid"] is True
    assert with_snapshot["open_tool_call_ids"] == ["call_write"]


@pytest.mark.asyncio
async def test_generic_exception_records_real_checkpoint_messages():
    snapshot = _next_model_snapshot()
    agent = AsyncMock()
    agent.aget_state = AsyncMock(return_value=snapshot)

    captured = await capture_checkpoint_snapshot(agent, {"configurable": {"thread_id": "t"}})
    diagnostics = build_exception_diagnostics(RuntimeError("boom"), captured)

    assert diagnostics["exception_type"] == "RuntimeError"
    assert diagnostics["provider_error_category"] == "runtime_error"
    assert diagnostics["checkpoint_message_count"] == 3
    assert diagnostics["checkpoint_valid"] is True


@pytest.mark.asyncio
async def test_checkpoint_capture_failure_preserves_primary_exception_context():
    agent = AsyncMock()
    agent.aget_state = AsyncMock(side_effect=RuntimeError("checkpoint unavailable"))

    captured = await capture_checkpoint_snapshot(agent, {"configurable": {"thread_id": "t"}})
    diagnostics = build_exception_diagnostics(ValueError("primary"), captured)

    assert diagnostics["exception_type"] == "ValueError"
    assert diagnostics["diagnostics_capture_error"] == "RuntimeError: checkpoint unavailable"
    assert diagnostics["checkpoint_state_kind"] == "empty"
    assert diagnostics["checkpoint_message_count"] == 0


def test_provider_timeout_exception_category_and_retry_compat_fields():
    diagnostics = build_checkpoint_diagnostics(
        snapshot=_next_model_snapshot(),
        exception=httpx.ReadTimeout(""),
    )

    assert diagnostics["provider_error_category"] == ProviderErrorCategory.PROVIDER_TIMEOUT.value
    assert diagnostics["recoverable_model_continuation"] is True
    assert diagnostics["retry_kind"] == "model_continuation"


def test_transcript_integrity_compat_alias_uses_payload_validation_category():
    exc = TranscriptIntegrityError([{"index": 1, "missing_tool_call_ids": ["call_x"]}])

    assert classify_provider_error(exc) == ProviderErrorCategory.PROVIDER_PAYLOAD_VALIDATION_ERROR
    assert exc.code == "PROVIDER_PAYLOAD_VALIDATION_ERROR"


def test_provider_payload_validation_exception_records_structured_issues():
    from agent.provider_payload_validator import ProviderPayloadValidationError

    exc = ProviderPayloadValidationError(
        [{
            "code": "orphan_tool_message",
            "index": 1,
            "role": "tool",
            "tool_call_id": "call_x",
        }],
        provider_family="deepseek",
    )

    diagnostics = build_checkpoint_diagnostics(
        snapshot=_next_model_snapshot(),
        exception=exc,
    )

    assert classify_provider_error(exc) == ProviderErrorCategory.PROVIDER_PAYLOAD_VALIDATION_ERROR
    assert diagnostics["provider_error_category"] == "provider_payload_validation_error"
    assert diagnostics["provider_payload_validation_error"] is True
    assert diagnostics["provider_family"] == "deepseek"
    assert diagnostics["provider_payload_issue_count"] == 1
    assert diagnostics["provider_payload_issues"][0]["tool_call_id"] == "call_x"


def test_diagnostics_keep_v043_compatibility_fields():
    diagnostics = build_checkpoint_diagnostics(snapshot=_next_model_snapshot())

    assert "checkpoint_interrupts_count" in diagnostics
    assert "checkpoint_bad_indices" in diagnostics
    assert "recoverable_model_continuation" in diagnostics
    assert "checkpoint_valid" in diagnostics
