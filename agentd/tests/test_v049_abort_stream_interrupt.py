"""v0.4.9 follow-up: abort polling during model token stream."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk


@pytest.mark.asyncio
async def test_messages_stream_abort_polling_returns_true_without_partial_persist(monkeypatch):
    from agent import executor

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            for idx in range(executor.ABORT_CHECK_CHUNK_INTERVAL + 5):
                yield "messages", (AIMessageChunk(content=f"token-{idx} "), {})

    events: list[dict] = []

    async def publish(_session_id, event):
        events.append(event)

    check_abort = AsyncMock(return_value=True)
    monkeypatch.setattr(executor, "_persist_message_incremental", AsyncMock())
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", AsyncMock())

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
        check_abort=check_abort,
    )

    assert aborted is True
    assert check_abort.await_count == 1
    assert len([event for event in events if event["event"] == "text_delta"]) == (
        executor.ABORT_CHECK_CHUNK_INTERVAL
    )
    executor._persist_message_incremental.assert_not_awaited()
    executor._persist_tool_group_atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_user_abort_clears_interrupt_permissions_and_abort_runs(monkeypatch):
    from agent import executor

    clear_interrupt = AsyncMock()
    cancel_abort = AsyncMock()
    cancel_permissions = AsyncMock()
    update_status = AsyncMock()
    publish = AsyncMock()

    monkeypatch.setattr("agent.scheduler.clear_interrupt", clear_interrupt)
    monkeypatch.setattr("agent.scheduler.cancel_queued_abort_runs", cancel_abort)
    monkeypatch.setattr(executor.perm_svc, "cancel_pending_by_session", cancel_permissions)
    monkeypatch.setattr(executor.session_svc, "update_session_status", update_status)

    db = AsyncMock()
    monkeypatch.setattr(executor, "AsyncSessionLocal", lambda: _AsyncDb(db))

    await executor._finalize_user_abort(
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    clear_interrupt.assert_awaited_once()
    cancel_abort.assert_awaited_once()
    cancel_permissions.assert_awaited_once()
    update_status.assert_awaited_once()
    db.commit.assert_awaited_once()
    publish.assert_awaited_once()
    assert publish.await_args.args[1]["status"] == "idle"
    assert publish.await_args.args[1]["reason"] == "user_abort"


class _AsyncDb:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False
