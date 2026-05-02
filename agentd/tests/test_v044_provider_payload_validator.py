"""v0.4.4 Phase C provider payload validator tests."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.provider_payload_validator import (
    ProviderPayloadValidationError,
    assert_provider_payload_valid,
    sanitize_provider_tool_adjacency,
    validate_provider_payload,
)


def test_valid_plain_conversation_passes():
    result = validate_provider_payload(
        [
            {"role": "system", "content": "You are AgentD."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        provider_family="openai_compatible",
    )

    assert result.ok is True
    assert result.generic_ok is True
    assert result.provider_specific_ok is True


def test_valid_assistant_tool_call_and_tool_result_passes():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
        ],
        provider_family="deepseek",
    )

    assert result.ok is True


def test_valid_parallel_tool_calls_can_return_in_any_order():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "knowledge_catalog", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "content": "catalog", "tool_call_id": "call_b"},
            {"role": "tool", "content": "ok", "tool_call_id": "call_a"},
        ],
        provider_family="qwen",
    )

    assert result.ok is True


def test_assistant_tool_call_missing_tool_result_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_missing",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }],
            },
        ],
        provider_family="deepseek",
    )

    assert result.ok is False
    assert result.issue_dicts[0]["code"] == "assistant_tool_call_missing_tool_result"
    assert result.issue_dicts[0]["missing_tool_call_ids"] == ["call_missing"]


def test_orphan_tool_result_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {"role": "tool", "content": "orphan", "tool_call_id": "call_orphan"},
        ],
        provider_family="openai_compatible",
    )

    assert result.ok is False
    assert result.issue_dicts[0]["code"] == "orphan_tool_message"
    assert result.issue_dicts[0]["tool_call_id"] == "call_orphan"


def test_unknown_tool_result_id_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_expected",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }],
            },
            {"role": "tool", "content": "wrong", "tool_call_id": "call_extra"},
        ],
        provider_family="glm",
    )

    assert result.ok is False
    issue = result.issue_dicts[0]
    assert issue["missing_tool_call_ids"] == ["call_expected"]
    assert issue["extra_tool_call_ids"] == ["call_extra"]


def test_duplicate_tool_call_id_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_dup",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "call_dup",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "call_dup"},
        ],
        provider_family="openai_compatible",
    )

    assert result.ok is False
    assert any(issue["code"] == "duplicate_tool_call_id" for issue in result.issue_dicts)


def test_empty_tool_call_id_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }],
            },
        ],
        provider_family="openai_compatible",
    )

    assert result.ok is False
    assert any(issue["code"] == "invalid_tool_call_id" for issue in result.issue_dicts)


def test_empty_tool_message_id_fails():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {"role": "tool", "content": "bad", "tool_call_id": ""},
        ],
        provider_family="openai_compatible",
    )

    assert result.ok is False
    assert result.issue_dicts[0]["code"] == "invalid_tool_message_id"


def test_unknown_role_fails():
    result = validate_provider_payload(
        [{"role": "critic", "content": "nope"}],
        provider_family="openai_compatible",
    )

    assert result.ok is False
    assert result.issue_dicts[0]["code"] == "unknown_role"


def test_openai_compatible_specific_hook_checks_function_shape():
    result = validate_provider_payload(
        [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"arguments": "{}"},
                }],
            },
            {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
        ],
        provider_family="minimax",
    )

    assert result.generic_ok is True
    assert result.provider_specific_ok is False
    assert any(
        issue["code"] == "missing_tool_call_function_name"
        for issue in result.issue_dicts
    )


def test_assert_provider_payload_valid_raises_structured_error():
    with pytest.raises(ProviderPayloadValidationError) as exc_info:
        assert_provider_payload_valid(
            [{"role": "tool", "content": "orphan", "tool_call_id": "call_orphan"}],
            provider_family="deepseek",
        )

    assert exc_info.value.provider_error_category == "provider_payload_validation_error"
    assert exc_info.value.provider_family == "deepseek"
    assert exc_info.value.issues[0]["index"] == 0


def test_deepseek_reasoning_continuation_state_is_preserved_in_payload():
    from agent.provider_reasoning import ProviderAwareChatOpenAI

    model = ProviderAwareChatOpenAI(
        model="deepseek-v4",
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1",
        provider_family="deepseek",
    )
    payload = model._get_request_payload([
        HumanMessage(content="use a tool"),
        AIMessage(
            content="",
            additional_kwargs={
                "reasoning_content": "carry state",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "skill", "arguments": "{\"action\":\"list\"}"},
                }],
            },
        ),
        ToolMessage(content="[]", tool_call_id="call_1"),
    ])

    assert payload["messages"][1]["reasoning_content"] == "carry state"
    assert payload["messages"][1]["tool_calls"][0]["id"] == "call_1"


def test_strict_fallback_sanitize_still_runs_validator():
    from agent.provider_reasoning import ProviderAwareChatOpenAI, TranscriptIntegrityError

    model = ProviderAwareChatOpenAI(
        model="deepseek-v4",
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1",
        provider_family="deepseek",
        strict_provider_compat_fallback=True,
    )

    with pytest.raises(TranscriptIntegrityError) as exc_info:
        model._get_request_payload([
            HumanMessage(content="run"),
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"arguments": "{}"},
                    }],
                },
            ),
            ToolMessage(content="ok", tool_call_id="call_1"),
        ])

    assert exc_info.value.issues[0]["code"] == "missing_tool_call_function_name"


def test_sanitizer_drops_invalid_group_before_validator():
    messages = sanitize_provider_tool_adjacency([
        {"role": "user", "content": "old"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_missing",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }],
        },
        {"role": "user", "content": "new"},
    ])

    assert messages == [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "new"},
    ]
