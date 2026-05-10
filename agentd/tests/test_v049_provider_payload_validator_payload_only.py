"""v0.4.9 Phase D: provider_payload_validator must operate on the actual
assembled provider payload, not on DB tail.

The brief explicitly forbids reverse-inferring provider payload validity from
``messages`` table contents. These tests pin three properties:

1. ``validate_provider_payload`` only inspects the message dicts the caller
   passes in — there is no DB read in the call path.
2. The validator catches the historical "assistant tool_calls without matching
   tool messages" failure on a hand-built payload (the provider would 400 here
   before v0.4.4).
3. A clean assembled payload remains valid even when the DB messages
   projection is dirty — proving the validator does not couple to DB state.
"""

from __future__ import annotations

import inspect

import pytest

from agent import provider_payload_validator as ppv


def test_validator_call_signature_takes_messages_not_session():
    """Defensive contract: the validator's public entry points accept a
    ``messages`` list. They must not require ``session`` / ``session_id`` /
    ``db`` parameters that would let them reverse-engineer payloads from
    DB state.
    """
    sig = inspect.signature(ppv.validate_provider_payload)
    assert "messages" in sig.parameters
    forbidden = {"session", "session_id", "db", "session_dir"}
    assert forbidden.isdisjoint(sig.parameters)

    sig2 = inspect.signature(ppv.assert_provider_payload_valid)
    assert "messages" in sig2.parameters
    assert forbidden.isdisjoint(sig2.parameters)


def test_validator_catches_assistant_tool_call_without_matching_tool_message():
    """The classic strict-provider 400 case: tool_calls open without a
    closing tool message. Validator must report it on the assembled payload.
    """
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        # No tool message closing call_a — followed directly by the next user.
        {"role": "user", "content": "now what"},
    ]

    result = ppv.validate_provider_payload(
        messages,
        provider_family="openai",
        model_id="gpt-x",
    )

    assert result.ok is False
    issue_codes = [issue.code for issue in result.issues]
    assert "assistant_tool_call_missing_tool_result" in issue_codes


def test_validator_does_not_read_db_for_clean_payload(monkeypatch):
    """A clean assembled payload validates regardless of any DB state.

    We monkeypatch ``session.service.list_messages`` to a sentinel that would
    raise on access; if the validator silently called it we would see the
    failure here.
    """
    sentinel_calls = {"count": 0}

    def _raise_if_called(*args, **kwargs):
        sentinel_calls["count"] += 1
        raise AssertionError("validator must not read DB messages")

    # Monkeypatch the most likely back-doors:
    import session.service as session_svc

    monkeypatch.setattr(session_svc, "list_messages", _raise_if_called)
    monkeypatch.setattr(session_svc, "get_last_message_seq", _raise_if_called)

    messages = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]

    result = ppv.validate_provider_payload(
        messages,
        provider_family="openai",
        model_id="gpt-x",
    )

    assert result.ok is True
    assert sentinel_calls["count"] == 0


def test_validator_failure_is_recoverable_not_terminal():
    """When validator surfaces an issue, the run should classify as
    recoverable (provider_protocol_tool_adjacency), not terminal — the
    session must remain usable so the user can retry or release.
    """
    from agent.runtime_error_classifier import RuntimeErrorClassifier

    envelope = RuntimeErrorClassifier.classify_error_text(
        "PROVIDER_PAYLOAD_VALIDATION_ERROR: assistant_tool_call_missing_tool_result"
    )

    assert envelope.severity == "recoverable"
    assert envelope.safe_to_continue_user_prompt is True
    assert envelope.category == "provider_protocol_tool_adjacency"


def test_sanitize_drops_invalid_tool_groups_only():
    """sanitize_provider_tool_adjacency must surgically remove broken groups
    while leaving everything else intact, so a clean tail still reaches the
    provider.
    """
    messages = [
        {"role": "user", "content": "go"},
        # Open group: no tool message — should be dropped.
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_open", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        # Followed by a clean unrelated turn.
        {"role": "user", "content": "ok try again"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_b", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_b", "content": "result"},
        {"role": "assistant", "content": "final"},
    ]

    sanitized = ppv.sanitize_provider_tool_adjacency(messages)

    # The open group (its assistant + no tool) is removed; remaining
    # transcript validates cleanly.
    result = ppv.validate_provider_payload(
        sanitized,
        provider_family="openai",
        model_id="gpt-x",
    )
    assert result.ok is True

    # The clean assistant turn remains.
    assistant_with_tools = [
        m for m in sanitized
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(assistant_with_tools) == 1
    assert assistant_with_tools[0]["tool_calls"][0]["id"] == "call_b"
