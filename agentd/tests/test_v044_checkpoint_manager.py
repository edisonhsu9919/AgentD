"""v0.4.4 Phase D checkpoint manager tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_manager import (
    CheckpointManager,
    candidate_tool_group_patch,
    missing_tail_tool_messages,
    rebuild_messages_with_repaired_tool_adjacency,
)


def test_candidate_tool_group_patch_builds_complete_group():
    ai = AIMessage(
        content="",
        id="ai-1",
        tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
    )
    tool = ToolMessage(content="ok", tool_call_id="call_1", name="bash")

    patch = candidate_tool_group_patch([], ai, [tool])

    assert patch == [ai, tool]


def test_missing_tail_tool_messages_returns_only_missing_results():
    ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "call_a", "name": "bash", "args": {}},
            {"id": "call_b", "name": "bash", "args": {}},
        ],
    )
    existing = ToolMessage(content="a", tool_call_id="call_a")
    missing = ToolMessage(content="b", tool_call_id="call_b")

    result = missing_tail_tool_messages([HumanMessage(content="run"), ai, existing], [missing])

    assert result == [missing]


def test_rebuild_messages_repairs_old_orphan_group():
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
    )
    tool = ToolMessage(content="ok", tool_call_id="call_1")
    messages = [
        HumanMessage(content="run"),
        ai,
        HumanMessage(content="interrupted"),
    ]

    rebuilt = rebuild_messages_with_repaired_tool_adjacency(messages, [tool])

    assert rebuilt == [messages[0], ai, tool, messages[2]]


@pytest.mark.asyncio
async def test_validate_continue_checkpoint_accepts_next_model_tool_result():
    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_1"),
        ]},
        next=("model",),
        interrupts=[],
    )
    agent = AsyncMock()
    agent.aget_state = AsyncMock(return_value=snapshot)

    await CheckpointManager.validate_continue_checkpoint(
        agent,
        {"configurable": {"thread_id": "s"}},
        "s",
    )


@pytest.mark.asyncio
async def test_validate_continue_checkpoint_rejects_open_hitl():
    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_write", "name": "file_write", "args": {}}],
            ),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_write"]})],
    )
    agent = AsyncMock()
    agent.aget_state = AsyncMock(return_value=snapshot)

    with pytest.raises(RuntimeError, match="hitl_open_tool_call"):
        await CheckpointManager.validate_continue_checkpoint(
            agent,
            {"configurable": {"thread_id": "s"}},
            "s",
        )
