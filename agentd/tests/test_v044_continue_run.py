"""v0.4.4 Phase B narrow continue run tests."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAgent:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def aget_state(self, config):
        return self.snapshot


def _snapshot(messages, *, next_nodes=("model",), interrupts=()):
    return SimpleNamespace(
        values={"messages": messages},
        next=next_nodes,
        interrupts=interrupts,
    )


def _closed_tool_result_snapshot():
    return _snapshot([
        HumanMessage(content="run"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "bash", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id="call_1", name="bash"),
    ])


def _invalid_snapshot():
    return _snapshot([
        HumanMessage(content="run"),
        ToolMessage(content="orphan", tool_call_id="call_missing", name="bash"),
    ])


def _hitl_snapshot():
    return _snapshot(
        [
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_2", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=("HumanInTheLoopMiddleware.after_model",),
        interrupts=(SimpleNamespace(value={"tool_call_ids": ["call_2"]}),),
    )


async def _run_execute_continue(monkeypatch, tmp_path, snapshot):
    from agent import executor

    session_id = str(uuid.uuid4())
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        id=uuid.UUID(session_id),
        user_id=user_id,
        parent_id=None,
        agent_id="assistant",
        model_id="test-model",
    )
    user = SimpleNamespace(id=user_id, workspace=str(tmp_path))
    db = AsyncMock()
    db.get.return_value = user

    fake_agent = _FakeAgent(snapshot)
    execute_graph = AsyncMock()

    monkeypatch.setattr(executor, "AsyncSessionLocal", lambda: _AsyncSessionContext(db))
    monkeypatch.setattr(executor.session_svc, "get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(executor, "build_agent", AsyncMock(return_value=fake_agent))
    monkeypatch.setattr(executor, "_execute_graph", execute_graph)

    await executor.execute_continue(
        session_id=session_id,
        publish=AsyncMock(),
        check_abort=AsyncMock(return_value=False),
        run_id="run-1",
    )
    return execute_graph


@pytest.mark.asyncio
async def test_execute_continue_validates_checkpoint_and_passes_no_user_message(
    monkeypatch,
    tmp_path,
):
    execute_graph = await _run_execute_continue(
        monkeypatch,
        tmp_path,
        _closed_tool_result_snapshot(),
    )

    execute_graph.assert_awaited_once()
    args = execute_graph.await_args.args
    kwargs = execute_graph.await_args.kwargs
    assert args[1] is None
    assert kwargs["skip_pre_microcompact"] is True


@pytest.mark.asyncio
async def test_execute_continue_rejects_invalid_checkpoint(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="not retryable"):
        await _run_execute_continue(monkeypatch, tmp_path, _invalid_snapshot())


@pytest.mark.asyncio
async def test_execute_continue_rejects_open_hitl_checkpoint(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="hitl_open_tool_call"):
        await _run_execute_continue(monkeypatch, tmp_path, _hitl_snapshot())


@pytest.mark.asyncio
async def test_worker_rejects_continue_payload_without_source_run_id():
    from agent.worker import AgentWorker

    worker = AgentWorker(worker_id="phase-v044-b")
    with pytest.raises(RuntimeError, match="source_run_id"):
        await worker._execute_continue(
            str(uuid.uuid4()),
            uuid.uuid4(),
            {"mode": "retry_model_node"},
        )
