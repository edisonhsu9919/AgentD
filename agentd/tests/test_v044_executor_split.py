"""v0.4.4 Phase D executor split compatibility tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_executor_checkpoint_wrapper_delegates_to_manager():
    from agent import executor

    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
    )
    tool = ToolMessage(content="ok", tool_call_id="call_1")

    with patch(
        "agent.executor.checkpoint_mgr.candidate_tool_group_patch",
        return_value=[ai, tool],
    ) as delegated:
        result = executor._candidate_tool_group_patch([], ai, [tool])

    delegated.assert_called_once()
    assert result == [ai, tool]


def test_executor_hitl_wrapper_delegates_to_runtime():
    from agent import executor

    snapshot = SimpleNamespace(values={"messages": []}, next=(), interrupts=[])
    with patch(
        "agent.executor.hitl_rt.HITLRuntime.snapshot_is_open_interrupt",
        return_value=True,
    ) as delegated:
        assert executor._snapshot_is_open_hitl_interrupt(snapshot) is True

    delegated.assert_called_once_with(snapshot)


@pytest.mark.asyncio
async def test_executor_message_persistence_wrapper_delegates():
    from agent import executor

    message = ToolMessage(content="ok", tool_call_id="call_1")
    with patch(
        "agent.executor.msg_persist.persist_message_incremental",
        new=AsyncMock(),
    ) as delegated:
        await executor._persist_message_incremental("00000000-0000-0000-0000-000000000001", message)

    delegated.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_translate_persists_ordinary_tool_group_atomically(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "list_dir", "args": {"path": "."}}],
    )
    tool_message = ToolMessage(content="ok", tool_call_id="call_1", name="list_dir")

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}
            yield "updates", {"tools": {"messages": [tool_message]}}

    events = []

    async def publish(_session_id, event):
        events.append(event)

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    assert aborted is False
    incremental.assert_not_awaited()
    atomic.assert_awaited_once_with(
        "00000000-0000-0000-0000-000000000001",
        ai_message,
        [tool_message],
        flush_reason="complete_tool_group",
    )
    assert [event["event"] for event in events] == ["tool_start", "tool_result"]


@pytest.mark.asyncio
async def test_stream_translate_waits_for_split_parallel_tool_results(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_1", "name": "list_dir", "args": {"path": "."}},
            {"id": "call_2", "name": "glob", "args": {"pattern": "*.py"}},
            {"id": "call_3", "name": "grep", "args": {"pattern": "ALPHA"}},
        ],
    )
    tool_1 = ToolMessage(content="one", tool_call_id="call_1", name="list_dir")
    tool_2 = ToolMessage(content="two", tool_call_id="call_2", name="glob")
    tool_3 = ToolMessage(content="three", tool_call_id="call_3", name="grep")

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}
            yield "updates", {"tools": {"messages": [tool_1]}}
            yield "updates", {"tools": {"messages": [tool_2]}}
            yield "updates", {"tools": {"messages": [tool_3]}}

    events = []

    async def publish(_session_id, event):
        events.append(event)

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    assert aborted is False
    incremental.assert_not_awaited()
    assert atomic.await_count == 1
    atomic.assert_awaited_once_with(
        "00000000-0000-0000-0000-000000000001",
        ai_message,
        [tool_1, tool_2, tool_3],
        flush_reason="complete_tool_group",
    )
    assert [event["event"] for event in events] == [
        "tool_start", "tool_start", "tool_start",
        "tool_result", "tool_result", "tool_result",
    ]


@pytest.mark.asyncio
async def test_stream_translate_flushes_incomplete_group_at_stream_end(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_1", "name": "list_dir", "args": {"path": "."}},
            {"id": "call_2", "name": "glob", "args": {"pattern": "*.py"}},
            {"id": "call_3", "name": "grep", "args": {"pattern": "ALPHA"}},
        ],
    )
    tool_1 = ToolMessage(content="one", tool_call_id="call_1", name="list_dir")
    tool_2 = ToolMessage(content="two", tool_call_id="call_2", name="glob")

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}
            yield "updates", {"tools": {"messages": [tool_1]}}
            yield "updates", {"tools": {"messages": [tool_2]}}

    async def publish(_session_id, _event):
        return None

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    assert aborted is False
    incremental.assert_not_awaited()
    atomic.assert_awaited_once_with(
        "00000000-0000-0000-0000-000000000001",
        ai_message,
        [tool_1, tool_2],
        flush_reason="stream_end_incomplete_tool_group",
    )


@pytest.mark.asyncio
async def test_stream_translate_hitl_interrupt_does_not_synthetic_close(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[{
            "id": "call_write",
            "name": "file_write",
            "args": {"path": "x.txt", "content": "x"},
        }],
    )
    snapshot = SimpleNamespace(
        values={"messages": [HumanMessage(content="write"), ai_message]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{
                "name": "file_write",
                "args": {"path": "x.txt", "content": "x"},
            }],
            "tool_call_ids": ["call_write"],
        })],
    )

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}

        async def aget_state(self, _config):
            return snapshot

    async def publish(_session_id, _event):
        return None

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    assert aborted is False
    incremental.assert_not_awaited()
    atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_translate_mixed_hitl_missing_auto_sibling_does_not_synthetic_close(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_list", "name": "list_dir", "args": {"path": "null"}},
            {"id": "call_bash", "name": "bash", "args": {"command": "echo HITL_OK"}},
        ],
    )
    snapshot = SimpleNamespace(
        values={"messages": [HumanMessage(content="inspect"), ai_message]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{
                "name": "bash",
                "args": {"command": "echo HITL_OK"},
            }],
            "tool_call_ids": ["call_bash"],
        })],
    )

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}

        async def aget_state(self, _config):
            return snapshot

    async def publish(_session_id, _event):
        return None

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
    )

    assert aborted is False
    incremental.assert_not_awaited()
    atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_translate_abort_does_not_persist_half_open_tool_call(monkeypatch):
    from agent import executor

    ai_message = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "list_dir", "args": {"path": "."}}],
    )

    class FakeAgent:
        async def astream(self, _input_data, *, config, stream_mode):
            yield "updates", {"model": {"messages": [ai_message]}}

    events = []

    async def publish(_session_id, event):
        events.append(event)

    monkeypatch.setattr(executor.settings, "message_persist_atomic_tool_group", True)
    monkeypatch.setattr(executor, "_is_subtask_waiting", AsyncMock(return_value=False))
    incremental = AsyncMock()
    atomic = AsyncMock()
    monkeypatch.setattr(executor, "_persist_message_incremental", incremental)
    monkeypatch.setattr(executor, "_persist_tool_group_atomic", atomic)

    aborted = await executor._stream_and_translate(
        FakeAgent(),
        {"messages": []},
        {},
        "00000000-0000-0000-0000-000000000001",
        publish,
        check_abort=AsyncMock(return_value=True),
    )

    assert aborted is True
    assert [event["event"] for event in events] == ["tool_start"]
    incremental.assert_not_awaited()
    atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_run_diagnostics_wrapper_delegates():
    from agent import executor

    agent = SimpleNamespace()
    messages = [HumanMessage(content="hi")]
    with patch(
        "agent.executor.run_diag.record_run_diagnostics",
        new=AsyncMock(),
    ) as delegated:
        await executor._record_run_diagnostics(agent, "s", messages)

    delegated.assert_awaited_once_with(agent, "s", messages, snapshot=None)
