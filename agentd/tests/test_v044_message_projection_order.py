"""v0.4.4 follow-up message projection order tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from agent.message_persistence import (
    load_existing_part_keys,
    part_dedupe_keys,
    persist_runtime_message_once,
    projection_can_append,
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


def test_user_prompt_message_ref_and_hash_keys_are_stable():
    keys = part_dedupe_keys({
        "type": "text",
        "content": "  hello   world ",
        "origin": "user_prompt",
        "message_ref": "msg-1",
    })

    assert keys[0] == "user_prompt:msg-1"
    assert keys[1].startswith("user_text_hash:")
    assert keys[1] == part_dedupe_keys({
        "type": "text",
        "content": "hello world",
        "origin": "user_prompt",
    })[0]


@pytest.mark.asyncio
async def test_checkpoint_human_message_dedupes_api_user_prompt(monkeypatch):
    session_id = uuid.uuid4()
    existing = [
        _db_message(1, "user", [{
            "type": "text",
            "content": "整理保险明细",
            "origin": "user_prompt",
            "message_ref": "msg-1",
        }])
    ]
    create_message = AsyncMock()
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.create_message",
        create_message,
    )

    keys = await load_existing_part_keys(object(), session_id)
    persisted = await persist_runtime_message_once(
        AsyncMock(),
        session_id,
        HumanMessage(
            content="整理保险明细",
            additional_kwargs={
                "origin": "user_prompt",
                "message_ref": "msg-1",
            },
        ),
        keys,
    )

    assert persisted is False
    create_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_human_message_legacy_hash_dedupes_api_user_prompt(monkeypatch):
    session_id = uuid.uuid4()
    existing = [
        _db_message(1, "user", [{
            "type": "text",
            "content": "整理保险明细",
        }])
    ]
    create_message = AsyncMock()
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.create_message",
        create_message,
    )

    keys = await load_existing_part_keys(object(), session_id)
    persisted = await persist_runtime_message_once(
        AsyncMock(),
        session_id,
        HumanMessage(
            content="  整理保险明细  ",
            additional_kwargs={"origin": "user_prompt"},
        ),
        keys,
    )

    assert persisted is False
    create_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_projection_guard_blocks_user_insert_between_tool_call_and_result(monkeypatch):
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=[
            _db_message(10, "assistant", [_tool_call_part("call_edit", "file_edit")]),
        ]),
    )

    allowed = await projection_can_append(
        object(),
        uuid.uuid4(),
        "user",
        [{"type": "text", "content": "duplicated prompt", "origin": "user_prompt"}],
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_discarded_projection_does_not_keep_tool_dedupe_key(monkeypatch):
    session_id = uuid.uuid4()
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=[
            _db_message(10, "assistant", [{
                **_tool_call_part("call_edit", "file_edit"),
                "projection_state": "discarded",
            }]),
        ]),
    )

    keys = await load_existing_part_keys(object(), session_id)

    assert "tool:tool_call:call_edit" not in keys


@pytest.mark.asyncio
async def test_projection_guard_allows_matching_tool_result_after_open_tool_call(monkeypatch):
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=[
            _db_message(10, "assistant", [_tool_call_part("call_edit", "file_edit")]),
        ]),
    )

    allowed = await projection_can_append(
        object(),
        uuid.uuid4(),
        "tool",
        [_tool_result_part("call_edit", "file_edit")],
    )

    assert allowed is True


@pytest.mark.asyncio
async def test_projection_guard_blocks_unrelated_tool_result(monkeypatch):
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=[
            _db_message(10, "assistant", [_tool_call_part("call_edit", "file_edit")]),
        ]),
    )

    allowed = await projection_can_append(
        object(),
        uuid.uuid4(),
        "tool",
        [_tool_result_part("call_other", "bash")],
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_persist_runtime_message_once_does_not_append_duplicate_user_in_open_group(monkeypatch):
    session_id = uuid.uuid4()
    existing = [
        _db_message(9, "user", [{
            "type": "text",
            "content": "整理保险明细",
            "origin": "user_prompt",
            "message_ref": "msg-1",
        }]),
        _db_message(10, "assistant", [_tool_call_part("call_edit", "file_edit")]),
    ]
    create_message = AsyncMock()
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.create_message",
        create_message,
    )

    keys = await load_existing_part_keys(object(), session_id)
    persisted = await persist_runtime_message_once(
        AsyncMock(),
        session_id,
        HumanMessage(
            content="整理保险明细",
            additional_kwargs={
                "origin": "user_prompt",
                "message_ref": "msg-1",
            },
        ),
        keys,
    )

    assert persisted is False
    create_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_runtime_message_once_appends_matching_tool_result_in_open_group(monkeypatch):
    session_id = uuid.uuid4()
    existing = [
        _db_message(10, "assistant", [_tool_call_part("call_edit", "file_edit")]),
    ]
    create_message = AsyncMock()
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.create_message",
        create_message,
    )

    persisted = await persist_runtime_message_once(
        AsyncMock(),
        session_id,
        ToolMessage(content="ok", tool_call_id="call_edit", name="file_edit"),
        set(),
    )

    assert persisted is True
    create_message.assert_awaited_once()
