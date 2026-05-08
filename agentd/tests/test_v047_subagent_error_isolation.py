"""v0.4.7 Phase D subagent error isolation tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _AsyncCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_child_failure_bridges_to_parent_without_parent_error(tmp_path):
    from agent.worker import AgentWorker

    child_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()

    child = SimpleNamespace(id=child_id, parent_id=parent_id)
    parent = SimpleNamespace(
        id=parent_id,
        user_id=user_id,
        status="subtask_waiting",
        agent_id="assistant",
        model_id="test-model",
    )
    task = SimpleNamespace(
        id=task_id,
        status="running",
        error=None,
        title="child task",
    )
    user = SimpleNamespace(id=user_id, workspace=str(tmp_path))
    continuation_run = SimpleNamespace(id=uuid.uuid4())

    db1 = AsyncMock()
    db1.get.side_effect = [child, parent]
    db2 = AsyncMock()
    db2.execute.return_value = _Result(task)
    db3 = AsyncMock()
    db3.get.return_value = user

    contexts = [_AsyncCtx(db1), _AsyncCtx(db2), _AsyncCtx(db3)]

    def _session_factory():
        return contexts.pop(0)

    worker = AgentWorker(worker_id="phase-d-subagent")
    worker._publish = AsyncMock()

    with (
        patch("agent.worker.AsyncSessionLocal", side_effect=_session_factory),
        patch("workspace.manager.get_session_dir", return_value=str(tmp_path)),
        patch("session.service.create_message", new=AsyncMock()) as create_message,
        patch("agent.worker.scheduler.enqueue_start", new=AsyncMock(return_value=continuation_run)) as enqueue,
    ):
        await worker._bridge_child_failure(str(child_id), "child exploded")

    assert task.status == "failed"
    assert task.error == "child exploded"
    create_message.assert_awaited_once()
    parts = create_message.await_args.kwargs["parts"]
    assert parts[0]["type"] == "subtask_result"
    assert parts[0]["status"] == "failed"
    enqueue.assert_awaited_once()
    published = [call.args[1] for call in worker._publish.await_args_list]
    assert {"event": "status_change", "status": "queued"} in published
    assert any(event.get("event") == "task_failed" for event in published)
    assert not any(event.get("status") == "error" for event in published)
