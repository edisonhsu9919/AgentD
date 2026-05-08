"""v0.4.7 Phase A runtime error classifier tests."""

from __future__ import annotations

import pytest

from agent.runtime_error_classifier import RuntimeErrorClassifier


@pytest.mark.parametrize(
    ("error", "category"),
    [
        ("APIConnectionError: Connection error.", "provider_transient"),
        ("APITimeoutError: Request timed out.", "provider_transient"),
        ("RemoteProtocolError: peer closed connection without sending complete message body", "provider_transient"),
        ("RateLimitError: Error code: 429", "provider_rate_limit"),
        ("APIError: 当前处于高峰时段，请稍后重试，触发速率限制", "provider_rate_limit"),
        ("ValueError: No generations found in stream.", "provider_empty_stream"),
        ("APIError: Context size has been exceeded.", "provider_context_overflow"),
        ("OpenAIContextOverflowError: context_length_exceeded", "provider_context_overflow"),
        ("The reasoning_content in the thinking mode must be passed back", "provider_protocol_reasoning"),
        ("assistant message with 'tool_calls' must be followed by tool messages", "provider_protocol_tool_adjacency"),
        ("RuntimeIntegrityError: db_tail_open_tool_call ids=['call_1']", "provider_protocol_tool_adjacency"),
        ("Model Not Exist: qwen-old", "provider_config_error"),
        ("unknown model MiniMax-M2.5", "provider_config_error"),
        ("Model config not found for session.model_id=MiniMax-M2.5", "provider_config_error"),
        ("TypeError: unexpected keyword argument 'top_k'", "provider_bad_request_params"),
        ("ToolLoopCircuitBreaker: blocked repeated tool call", "tool_loop_breaker"),
        ("Auto-approved HITL interrupt did not advance", "hitl_resume_mismatch"),
        ("Number of human decisions does not match hanging tool calls", "hitl_resume_mismatch"),
    ],
)
def test_runtime_error_classifier_maps_historical_errors(error: str, category: str):
    envelope = RuntimeErrorClassifier.classify_error_text(error)

    assert envelope.category == category
    assert envelope.severity == "recoverable"
    assert envelope.safe_to_continue_user_prompt is True


def test_context_overflow_carries_reactive_compact_hint():
    envelope = RuntimeErrorClassifier.classify_error_text(
        "APIError: maximum context length exceeded"
    )

    assert envelope.category == "provider_context_overflow"
    assert envelope.next_action == "recover"
    assert envelope.auto_recovery["allowed"] is True
    assert envelope.auto_recovery["next_strategy"] == "reactive_compact_then_continue"


def test_internal_invariant_is_terminal():
    envelope = RuntimeErrorClassifier.classify_error_text(
        "AssertionError: internal invariant violated"
    )

    assert envelope.category == "internal_invariant_violation"
    assert envelope.severity == "terminal"
    assert envelope.safe_to_continue_user_prompt is False
    assert envelope.next_action == "terminal"
