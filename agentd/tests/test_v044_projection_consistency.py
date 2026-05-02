"""v0.4.4 projection consistency repair tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.projection_consistency import (
    inspect_db_checkpoint_projection,
    repair_db_projection_ahead,
)
from agent.runtime_integrity import inspect_db_transcript_tail


def _db_message(seq: int, role: str, parts: list[dict]):
    return SimpleNamespace(id=uuid.uuid4(), seq=seq, role=role, parts=parts)


def _tool_call_part(tool_call_id: str, name: str = "bash"):
    return {
        "type": "tool_call",
        "tool_call_id": tool_call_id,
        "tool_name": name,
        "input": {},
    }


class _FakeDb:
    def __init__(self, messages):
        self.messages = {message.id: message for message in messages}

    async def get(self, _model, message_id):
        return self.messages.get(message_id)

    async def execute(self, statement):
        values = getattr(statement, "_values", {}) or {}
        parts_value = None
        for key, value in values.items():
            if str(getattr(key, "key", key)) == "parts":
                parts_value = value
                break
        new_parts = getattr(parts_value, "value", parts_value)
        for message in self.messages.values():
            if new_parts is not None:
                message.parts = new_parts


@pytest.mark.asyncio
async def test_detects_db_projection_ahead_of_checkpoint(monkeypatch):
    session_id = uuid.uuid4()
    db_messages = [
        _db_message(14, "assistant", [_tool_call_part("call_todo", "todo_update")]),
        _db_message(15, "tool", [{
            "type": "tool_result",
            "tool_call_id": "call_todo",
            "output": "ok",
        }]),
        _db_message(16, "assistant", [_tool_call_part("call_bash", "bash")]),
    ]
    checkpoint_messages = [
        HumanMessage(content="make slides"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_todo", "name": "todo_update", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id="call_todo", name="todo_update"),
    ]
    monkeypatch.setattr(
        "agent.projection_consistency.session_svc.list_messages",
        AsyncMock(return_value=db_messages),
    )

    report = await inspect_db_checkpoint_projection(
        object(),
        session_id,
        checkpoint_messages,
    )

    assert report.is_db_projection_ahead is True
    assert report.db_ahead_tool_call_ids == ["call_bash"]
    assert report.recommended_action == "discard_uncommitted_db_projection"
    assert report.repairable_message_seqs == [16]


@pytest.mark.asyncio
async def test_repair_discards_db_only_projection_and_tail_becomes_clean(monkeypatch):
    session_id = uuid.uuid4()
    dangling = _db_message(16, "assistant", [
        {"type": "reasoning", "content": "checking"},
        _tool_call_part("call_bash", "bash"),
    ])
    db_messages = [dangling]
    monkeypatch.setattr(
        "agent.projection_consistency.session_svc.list_messages",
        AsyncMock(return_value=db_messages),
    )
    report = await inspect_db_checkpoint_projection(object(), session_id, [])

    repair = await repair_db_projection_ahead(_FakeDb(db_messages), report)

    assert repair.repaired is True
    assert repair.discarded_tool_call_ids == ["call_bash"]
    assert all(part["projection_state"] == "discarded" for part in dangling.parts)
    assert inspect_db_transcript_tail(db_messages).clean is True


@pytest.mark.asyncio
async def test_checkpoint_has_same_tool_call_is_not_db_ahead(monkeypatch):
    session_id = uuid.uuid4()
    db_messages = [
        _db_message(16, "assistant", [_tool_call_part("call_bash", "bash")]),
    ]
    checkpoint_messages = [
        HumanMessage(content="check"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_bash", "name": "bash", "args": {}}],
        ),
    ]
    monkeypatch.setattr(
        "agent.projection_consistency.session_svc.list_messages",
        AsyncMock(return_value=db_messages),
    )

    report = await inspect_db_checkpoint_projection(
        object(),
        session_id,
        checkpoint_messages,
    )

    assert report.is_db_projection_ahead is False
    assert report.blocked_reason == "open_tool_call_present_in_checkpoint"


@pytest.mark.asyncio
async def test_db_ahead_with_later_user_message_is_not_auto_repairable(monkeypatch):
    session_id = uuid.uuid4()
    db_messages = [
        _db_message(16, "assistant", [_tool_call_part("call_bash", "bash")]),
        _db_message(17, "user", [{"type": "text", "content": "continue"}]),
    ]
    monkeypatch.setattr(
        "agent.projection_consistency.session_svc.list_messages",
        AsyncMock(return_value=db_messages),
    )

    report = await inspect_db_checkpoint_projection(object(), session_id, [])

    assert report.is_db_projection_ahead is False
    assert report.blocked_reason == "later_messages_exist"
