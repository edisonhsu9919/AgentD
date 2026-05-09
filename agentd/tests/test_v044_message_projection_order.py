"""v0.4.4 follow-up message projection order tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.message_persistence import (
    build_persistable_message_parts,
    complete_tool_group_messages,
    load_existing_part_keys,
    part_dedupe_keys,
    persist_tool_group_atomic,
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


def test_complete_tool_group_messages_synthesizes_missing_result():
    ai_message = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_a", "name": "list_dir", "args": {"path": "."}},
            {"id": "call_b", "name": "grep", "args": {"pattern": "x"}},
        ],
    )
    tool_messages = [
        ToolMessage(content="ok", tool_call_id="call_a", name="list_dir"),
    ]

    completed = complete_tool_group_messages(
        ai_message,
        tool_messages,
        flush_reason="stream_end_incomplete_tool_group",
    )

    assert [message.tool_call_id for message in completed] == ["call_a", "call_b"]
    assert completed[0].content == "ok"
    assert "TOOL_GROUP_ATOMICITY_ERROR" in completed[1].content
    assert completed[1].additional_kwargs["is_error"] is True
    assert completed[1].additional_kwargs["synthetic_close"] is True
    assert completed[1].additional_kwargs["error_code"] == "TOOL_GROUP_ATOMICITY_ERROR"
    assert completed[1].additional_kwargs["tool_group_flush_reason"] == "stream_end_incomplete_tool_group"
    assert completed[1].additional_kwargs["required_tool_call_ids"] == ["call_a", "call_b"]
    assert completed[1].additional_kwargs["received_tool_result_ids"] == ["call_a"]
    assert completed[1].additional_kwargs["missing_tool_result_ids"] == ["call_b"]


def test_synthetic_tool_result_part_keeps_atomicity_metadata():
    tool_message = ToolMessage(
        content="TOOL_GROUP_ATOMICITY_ERROR: missing",
        tool_call_id="call_b",
        name="grep",
        additional_kwargs={
            "is_error": True,
            "synthetic_close": True,
            "error_code": "TOOL_GROUP_ATOMICITY_ERROR",
            "tool_group_flush_reason": "stream_end_incomplete_tool_group",
            "required_tool_call_ids": ["call_a", "call_b"],
            "received_tool_result_ids": ["call_a"],
            "missing_tool_result_ids": ["call_b"],
        },
    )

    role, parts, _is_summary = build_persistable_message_parts(tool_message)

    assert role == "tool"
    assert parts[0]["is_error"] is True
    assert parts[0]["synthetic_close"] is True
    assert parts[0]["error_code"] == "TOOL_GROUP_ATOMICITY_ERROR"
    assert parts[0]["tool_group_flush_reason"] == "stream_end_incomplete_tool_group"
    assert parts[0]["missing_tool_result_ids"] == ["call_b"]


@pytest.mark.asyncio
async def test_persist_tool_group_atomic_writes_assistant_and_all_results(monkeypatch):
    session_id = uuid.uuid4()
    created: list[tuple[str, list[dict]]] = []

    class FakeDb:
        commits = 0
        rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    fake_db = FakeDb()

    class FakeSession:
        async def __aenter__(self):
            return fake_db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_create_message(_db, *, session_id, role, parts, is_summary=False):
        created.append((role, parts))

    monkeypatch.setattr(
        "agent.message_persistence.AsyncSessionLocal",
        lambda: FakeSession(),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.list_messages",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "agent.message_persistence.session_svc.create_message",
        fake_create_message,
    )

    await persist_tool_group_atomic(
        str(session_id),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call_a", "name": "list_dir", "args": {"path": "."}},
                {"id": "call_b", "name": "grep", "args": {"pattern": "x"}},
            ],
        ),
        [
            ToolMessage(content="files", tool_call_id="call_a", name="list_dir"),
            ToolMessage(content="matches", tool_call_id="call_b", name="grep"),
        ],
    )

    assert [role for role, _parts in created] == ["assistant", "tool", "tool"]
    assert created[0][1][0]["type"] == "tool_call"
    assert created[0][1][0]["tool_call_id"] == "call_a"
    assert created[1][1][0]["tool_call_id"] == "call_a"
    assert created[2][1][0]["tool_call_id"] == "call_b"
    assert fake_db.commits == 1
    assert fake_db.rollbacks == 0
