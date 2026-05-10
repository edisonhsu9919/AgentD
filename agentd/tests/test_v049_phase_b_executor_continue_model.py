"""v0.4.9 Phase B Finding 1: executor _finalize must not raise on CONTINUE_MODEL.

Phase A only handled CONTINUE_MODEL inside the worker terminal helper
(_assert_run_returned_terminal_state). Real agent runs reach _finalize first,
which previously raised RuntimeIntegrityError for any non-FINALIZE_IDLE /
ENTER_WAITING / ENTER_SUBTASK_WAITING action — including CONTINUE_MODEL. That
made the worker's continue-enqueue path unreachable for the historical
``checkpoint_next_model_after_tool_result`` fatal scenario.

These tests pin the new contract:
- _finalize on CONTINUE_MODEL: no raise.
- diagnostics still recorded.
- session.status set to "idle" (not "error"), so worker re-evaluates and
  enqueues a continue run via _enqueue_terminal_continue_model.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeGateDecision,
    RuntimeIntegrityError,
)


def _continue_model_decision() -> RuntimeGateDecision:
    return RuntimeGateDecision(
        action=RuntimeGateAction.CONTINUE_MODEL,
        reason="checkpoint_next_model_after_tool_result",
        checkpoint_state_kind="next_model_after_tool_result",
        is_provider_payload_ready=True,
    )


def _build_finalize_agent(messages):
    snapshot = SimpleNamespace(values={"messages": messages})

    class FakeAgent:
        async def aget_state(self, _config):
            return snapshot

    agent = FakeAgent()
    agent._user_id = "user-1"
    agent._user_root = "/tmp/u"
    agent._microcompact_result = None
    return agent


@pytest.mark.asyncio
async def test_finalize_does_not_raise_on_continue_model(monkeypatch):
    """CONTINUE_MODEL must NOT propagate as RuntimeIntegrityError out of _finalize."""
    from agent import executor

    messages = [
        HumanMessage(content="run"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id="call_1", name="bash"),
    ]
    agent = _build_finalize_agent(messages)

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    monkeypatch.setattr(executor, "_record_run_diagnostics", AsyncMock())
    monkeypatch.setattr(executor, "_update_db_status", AsyncMock())

    decide_mock = AsyncMock(return_value=(_continue_model_decision(), None))
    monkeypatch.setattr(executor, "_decide_runtime_terminal_state", decide_mock)

    publish = AsyncMock()

    # Must NOT raise RuntimeIntegrityError.
    await executor._finalize(agent, {}, str(uuid.uuid4()), publish)


@pytest.mark.asyncio
async def test_finalize_continue_model_records_diagnostics(monkeypatch):
    """_finalize on CONTINUE_MODEL still records run diagnostics."""
    from agent import executor

    agent = _build_finalize_agent([HumanMessage(content="hi")])

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    record_mock = AsyncMock()
    monkeypatch.setattr(executor, "_record_run_diagnostics", record_mock)
    monkeypatch.setattr(executor, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        executor, "_decide_runtime_terminal_state",
        AsyncMock(return_value=(_continue_model_decision(), None)),
    )

    publish = AsyncMock()
    await executor._finalize(agent, {}, str(uuid.uuid4()), publish)

    # _record_run_diagnostics is called at least once before/around the gate
    # decision branch. We just need to confirm it ran.
    assert record_mock.await_count >= 1


@pytest.mark.asyncio
async def test_finalize_continue_model_sets_status_idle_not_error(monkeypatch):
    """_finalize on CONTINUE_MODEL sets status=idle so worker re-evaluates clean."""
    from agent import executor

    agent = _build_finalize_agent([HumanMessage(content="hi")])

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    monkeypatch.setattr(executor, "_record_run_diagnostics", AsyncMock())
    update_mock = AsyncMock()
    monkeypatch.setattr(executor, "_update_db_status", update_mock)
    monkeypatch.setattr(
        executor, "_decide_runtime_terminal_state",
        AsyncMock(return_value=(_continue_model_decision(), None)),
    )

    publish = AsyncMock()
    await executor._finalize(agent, {}, str(uuid.uuid4()), publish)

    # Must have called _update_db_status with "idle" — never "error".
    statuses = [call.args[1] for call in update_mock.await_args_list]
    assert "idle" in statuses
    assert "error" not in statuses


@pytest.mark.asyncio
async def test_finalize_continue_model_publishes_status_change(monkeypatch):
    """_finalize on CONTINUE_MODEL publishes a status_change with continue trigger."""
    from agent import executor

    agent = _build_finalize_agent([HumanMessage(content="hi")])

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    monkeypatch.setattr(executor, "_record_run_diagnostics", AsyncMock())
    monkeypatch.setattr(executor, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        executor, "_decide_runtime_terminal_state",
        AsyncMock(return_value=(_continue_model_decision(), None)),
    )

    publish = AsyncMock()
    await executor._finalize(agent, {}, str(uuid.uuid4()), publish)

    # Must have published an executor_finalize_continue_model status_change.
    payloads = [call.args[1] for call in publish.await_args_list]
    assert any(
        p.get("event") == "status_change"
        and p.get("status") == "idle"
        and p.get("trigger") == "executor_finalize_continue_model"
        and p.get("reason") == "checkpoint_next_model_after_tool_result"
        for p in payloads
    )


@pytest.mark.asyncio
async def test_finalize_still_raises_on_real_fail_integrity_error(monkeypatch):
    """_finalize must keep raising RuntimeIntegrityError for genuinely fatal gate decisions.

    Phase B narrows the soft-return path to CONTINUE_MODEL only. Real
    integrity failures (e.g. checkpoint corruption, unsupported state) must
    still propagate so the worker error path classifies them and surfaces
    diagnostics.
    """
    from agent import executor

    agent = _build_finalize_agent([HumanMessage(content="hi")])
    fatal = RuntimeGateDecision(
        action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
        reason="checkpoint_invalid:invalid_orphan_tool_call",
        checkpoint_state_kind="invalid_orphan_tool_call",
    )

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    monkeypatch.setattr(executor, "_record_run_diagnostics", AsyncMock())
    monkeypatch.setattr(executor, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        executor, "_decide_runtime_terminal_state",
        AsyncMock(return_value=(fatal, None)),
    )

    publish = AsyncMock()
    with pytest.raises(RuntimeIntegrityError):
        await executor._finalize(agent, {}, str(uuid.uuid4()), publish)
