"""v0.4.4 Phase D HITL runtime helper tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from agent.checkpoint_state import CheckpointStateKind, classify_checkpoint_snapshot
from agent.hitl_runtime import HITLRuntime


def _hitl_snapshot():
    return SimpleNamespace(
        values={"messages": [
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_write",
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{
                "name": "file_write",
                "args": {"path": "x.txt", "content": "x"},
            }],
            "tool_call_ids": ["call_write"],
        })],
    )


def test_resume_input_detection():
    assert HITLRuntime.is_resume_input(Command(resume={"decisions": []})) is True
    assert HITLRuntime.is_resume_input(None) is False


def test_snapshot_is_open_interrupt_for_hitl_tool_call():
    assert HITLRuntime.snapshot_is_open_interrupt(_hitl_snapshot()) is True


def test_extract_tool_call_ids_prefers_interrupt_payload():
    assert HITLRuntime.extract_tool_call_ids(_hitl_snapshot()) == ["call_write"]


def test_extract_unclosed_action_requests_from_next_boundary():
    snapshot = _hitl_snapshot()
    snapshot.interrupts = []

    actions, tool_call_ids = HITLRuntime.extract_unclosed_action_requests(snapshot)

    assert actions == [{
        "name": "file_write",
        "args": {"path": "x.txt", "content": "x"},
    }]
    assert tool_call_ids == ["call_write"]


def test_batch_key_is_stable_with_missing_tool_call_id():
    key = HITLRuntime.interrupt_batch_key(
        [{"name": "file_write", "args": {"path": "x.txt"}}],
        [""],
    )

    assert key == ("idx:0:file_write:{'path': 'x.txt'}",)


def test_resolved_interrupt_is_not_open():
    snapshot = _hitl_snapshot()
    snapshot.values["messages"].append(ToolMessage(content="ok", tool_call_id="call_write"))

    assert HITLRuntime.snapshot_interrupt_already_resolved(snapshot) is True
    assert HITLRuntime.snapshot_is_open_interrupt(snapshot) is False


def test_mixed_group_after_auto_sibling_has_only_hitl_open():
    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_glob", "name": "glob", "args": {"pattern": "**/*"}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_glob", name="glob"),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{"name": "bash", "args": {"command": "pwd"}}],
            "tool_call_ids": ["call_bash"],
        })],
    )

    state = classify_checkpoint_snapshot(snapshot)

    assert state.state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL
    assert state.open_tool_call_ids == ["call_bash"]
    assert HITLRuntime.snapshot_is_open_interrupt(snapshot) is True


@pytest.mark.asyncio
async def test_mixed_group_closes_auto_sibling_before_hitl(monkeypatch):
    from agent import executor

    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_glob", "name": "glob", "args": {"pattern": "**/*"}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{"name": "bash", "args": {"command": "pwd"}}],
            "tool_call_ids": ["call_bash"],
        })],
    )
    agent = SimpleNamespace(
        _user_id="u1",
        _user_root="/tmp/user",
        _workspace_dir="/tmp/user/sessions/s1",
        _run_id="run-1",
    )
    publish = AsyncMock()
    persist_messages = AsyncMock()
    persist_incremental = AsyncMock()
    update_tools = AsyncMock(return_value={"configurable": {"thread_id": "s1"}})

    monkeypatch.setattr(executor, "_persist_messages", persist_messages)
    monkeypatch.setattr(executor, "_persist_message_incremental", persist_incremental)
    monkeypatch.setattr(executor, "_aupdate_messages_as_tools", update_tools)
    monkeypatch.setattr(
        "tools.registry.execute_registered_tool",
        AsyncMock(return_value="glob-result"),
    )

    tool_messages = await executor._close_auto_sibling_tool_calls_before_hitl(
        agent,
        {"configurable": {"thread_id": "s1"}},
        "s1",
        "/tmp/user/sessions/s1",
        snapshot,
        ["call_bash"],
        publish,
    )

    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call_glob"
    assert tool_messages[0].name == "glob"
    persist_messages.assert_awaited_once()
    persist_incremental.assert_awaited_once()
    update_tools.assert_awaited_once()
    assert publish.await_count == 2


@pytest.mark.asyncio
async def test_resume_closes_approved_hitl_sibling_before_graph_resume(monkeypatch):
    from agent import executor

    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_glob", "name": "glob", "args": {"pattern": "**/*"}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_glob", name="glob"),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{"name": "bash", "args": {"command": "pwd"}}],
            "tool_call_ids": ["call_bash"],
        })],
    )
    agent = SimpleNamespace(
        _user_id="u1",
        _user_root="/tmp/user",
        _workspace_dir="/tmp/user/sessions/s1",
        _run_id="run-1",
    )
    publish = AsyncMock()
    persist_messages = AsyncMock()
    persist_incremental = AsyncMock()
    update_tools = AsyncMock(return_value={"configurable": {"thread_id": "s1"}})
    mark_consumed = AsyncMock()

    monkeypatch.setattr(executor, "_persist_messages", persist_messages)
    monkeypatch.setattr(executor, "_persist_message_incremental", persist_incremental)
    monkeypatch.setattr(executor, "_aupdate_messages_as_tools", update_tools)
    monkeypatch.setattr(
        executor,
        "_mark_resolved_hitl_permissions_consumed",
        mark_consumed,
    )
    monkeypatch.setattr(
        "tools.registry.execute_registered_tool",
        AsyncMock(return_value="pwd-result"),
    )

    tool_messages = await executor._close_resolved_hitl_tool_calls_before_resume(
        agent,
        {"configurable": {"thread_id": "s1"}},
        "s1",
        "/tmp/user/sessions/s1",
        snapshot,
        [{"type": "approve"}],
        publish,
    )

    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call_bash"
    assert tool_messages[0].name == "bash"
    assert tool_messages[0].content == "pwd-result"
    persist_messages.assert_awaited_once()
    persist_incremental.assert_awaited_once()
    update_tools.assert_awaited_once()
    mark_consumed.assert_awaited_once_with("s1", ["call_bash"])
    assert publish.await_count == 2


@pytest.mark.asyncio
async def test_resume_closes_hitl_sibling_even_when_checkpoint_next_is_model(monkeypatch):
    from agent import executor

    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="inspect"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_list", "name": "list_dir", "args": {"path": "."}},
                    {"id": "call_bash", "name": "bash", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="files", tool_call_id="call_list", name="list_dir"),
        ]},
        next=("model",),
        interrupts=[],
    )
    agent = SimpleNamespace(
        _user_id="u1",
        _user_root="/tmp/user",
        _workspace_dir="/tmp/user/sessions/s1",
        _run_id="run-1",
    )
    publish = AsyncMock()
    mark_consumed = AsyncMock()

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_message_incremental", AsyncMock())
    monkeypatch.setattr(
        executor,
        "_aupdate_messages_as_tools",
        AsyncMock(return_value={"configurable": {"thread_id": "s1"}}),
    )
    monkeypatch.setattr(
        executor,
        "_mark_resolved_hitl_permissions_consumed",
        mark_consumed,
    )
    monkeypatch.setattr(
        "tools.registry.execute_registered_tool",
        AsyncMock(return_value="pwd-result"),
    )

    tool_messages = await executor._close_resolved_hitl_tool_calls_before_resume(
        agent,
        {"configurable": {"thread_id": "s1"}},
        "s1",
        "/tmp/user/sessions/s1",
        snapshot,
        [{"type": "approve"}],
        publish,
    )

    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call_bash"
    assert tool_messages[0].content == "pwd-result"
    mark_consumed.assert_awaited_once_with("s1", ["call_bash"])


@pytest.mark.asyncio
async def test_resume_denied_hitl_sibling_writes_error_tool_result(monkeypatch):
    from agent import executor

    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {"command": "pwd"}}],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{"name": "bash", "args": {"command": "pwd"}}],
            "tool_call_ids": ["call_bash"],
        })],
    )
    agent = SimpleNamespace(_user_id="u1", _user_root="/tmp/user", _run_id="run-1")
    publish = AsyncMock()
    execute_tool = AsyncMock(return_value="should-not-run")

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_message_incremental", AsyncMock())
    monkeypatch.setattr(
        executor,
        "_aupdate_messages_as_tools",
        AsyncMock(return_value={"configurable": {"thread_id": "s1"}}),
    )
    monkeypatch.setattr(
        executor,
        "_mark_resolved_hitl_permissions_consumed",
        AsyncMock(),
    )
    monkeypatch.setattr("tools.registry.execute_registered_tool", execute_tool)

    tool_messages = await executor._close_resolved_hitl_tool_calls_before_resume(
        agent,
        {"configurable": {"thread_id": "s1"}},
        "s1",
        "/tmp/user/sessions/s1",
        snapshot,
        [{"type": "reject", "message": "nope"}],
        publish,
    )

    assert tool_messages[0].tool_call_id == "call_bash"
    assert tool_messages[0].content == "nope"
    assert tool_messages[0].additional_kwargs["is_error"] is True
    execute_tool.assert_not_awaited()
    assert publish.await_args_list[-1].args[1]["is_error"] is True


@pytest.mark.asyncio
async def test_resume_approved_hitl_tool_failure_still_closes_group(monkeypatch):
    from agent import executor

    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {"command": "pwd"}}],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={
            "action_requests": [{"name": "bash", "args": {"command": "pwd"}}],
            "tool_call_ids": ["call_bash"],
        })],
    )
    agent = SimpleNamespace(_user_id="u1", _user_root="/tmp/user", _run_id="run-1")
    publish = AsyncMock()

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_message_incremental", AsyncMock())
    monkeypatch.setattr(
        executor,
        "_aupdate_messages_as_tools",
        AsyncMock(return_value={"configurable": {"thread_id": "s1"}}),
    )
    monkeypatch.setattr(
        executor,
        "_mark_resolved_hitl_permissions_consumed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "tools.registry.execute_registered_tool",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    tool_messages = await executor._close_resolved_hitl_tool_calls_before_resume(
        agent,
        {"configurable": {"thread_id": "s1"}},
        "s1",
        "/tmp/user/sessions/s1",
        snapshot,
        [{"type": "approve"}],
        publish,
    )

    assert tool_messages[0].tool_call_id == "call_bash"
    assert tool_messages[0].content == "boom"
    assert tool_messages[0].additional_kwargs["is_error"] is True
    assert publish.await_args_list[-1].args[1]["is_error"] is True
