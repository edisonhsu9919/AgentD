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
