"""v0.4.4 late child bridge tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, *, parent, children, tasks, parent_messages=None, child_messages=None):
        self.parent = parent
        self.children = {child.id: child for child in children}
        self.tasks = list(tasks)
        self.parent_messages = list(parent_messages or [])
        self.child_messages = child_messages or {
            child_id: [SimpleNamespace(
                role="assistant",
                parts=[{"type": "text", "content": f"child {child_id} done"}],
            )]
            for child_id in self.children
        }
        self.committed = False
        self.rolled_back = False
        self.agent_run_queries = 0
        self.message_queries = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, item_id):
        name = getattr(model, "__name__", "")
        if name == "Session" and item_id == self.parent.id:
            return self.parent
        if name == "Session" and item_id in self.children:
            return self.children[item_id]
        return None

    async def execute(self, stmt):
        entity = (getattr(stmt, "column_descriptions", None) or [{}])[0].get("entity")
        name = getattr(entity, "__name__", "")
        text = str(stmt)
        if name == "SessionTask" or "FROM session_tasks" in text:
            return _Result([
                task
                for task in self.tasks
                if task.status in {"queued", "running", "waiting"}
            ])
        if name == "AgentRun" or "FROM agent_runs" in text:
            self.agent_run_queries += 1
            if "agent_runs.status IN" in text:
                return _Result([])
            return _Result([SimpleNamespace(status="completed", error=None)])
        if name == "Message" or "FROM messages" in text:
            self.message_queries += 1
            if "messages.session_id = :session_id_1" in text and "messages.role" in text:
                child_index = min(
                    max((self.message_queries - 2) // 3, 0),
                    len(self.tasks) - 1,
                )
                child_id = self.tasks[child_index].child_session_id
                return _Result(self.child_messages.get(child_id, []))
            return _Result(self.parent_messages)
        return _Result([])

    def add(self, item):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_idle_parent_late_children_bridge_and_coalesce_one_continuation(monkeypatch, tmp_path):
    from agent import subtask_bridge

    parent_id = uuid.uuid4()
    child_ids = [uuid.uuid4(), uuid.uuid4()]
    parent = SimpleNamespace(
        id=parent_id,
        user_id=uuid.uuid4(),
        status="idle",
        agent_id="assistant",
        model_id="test-model",
    )
    children = [SimpleNamespace(id=child_id, status="idle") for child_id in child_ids]
    tasks = [
        SimpleNamespace(
            id=uuid.uuid4(),
            session_id=parent_id,
            task_kind="child_session",
            blocking_mode="blocking",
            status="running",
            child_session_id=child_id,
            title=f"Batch {idx + 1}",
            result_ref=None,
        )
        for idx, child_id in enumerate(child_ids)
    ]
    fake_db = _FakeDb(parent=parent, children=children, tasks=tasks)
    created_messages = []
    enqueued = []

    monkeypatch.setattr(subtask_bridge, "AsyncSessionLocal", lambda: fake_db)
    monkeypatch.setattr(subtask_bridge, "_parent_session_dir", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr(subtask_bridge, "_can_append_parent_bridge", AsyncMock(return_value=True))
    monkeypatch.setattr(
        subtask_bridge,
        "repair_parent_checkpoint_before_subtask_continuation",
        AsyncMock(return_value={"provider_payload_preflight_ok": True}),
    )

    async def fake_create_message(db, *, session_id, role, parts, is_summary=False, token_usage=None):
        created_messages.append({"session_id": session_id, "role": role, "parts": parts})
        return SimpleNamespace()

    monkeypatch.setattr("session.service.create_message", fake_create_message)

    async def fake_enqueue(db, parent, parent_session_id, parent_session_dir, bridged_task_ids):
        enqueued.append(list(bridged_task_ids))
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(subtask_bridge, "_enqueue_parent_continuation", fake_enqueue)

    result = await subtask_bridge.bridge_reconcilable_child_tasks(parent_id)

    assert sorted(result.bridged_task_ids) == sorted(str(task.id) for task in tasks)
    assert sorted(result.completed_task_ids) == sorted(str(task.id) for task in tasks)
    assert len(created_messages) == 2
    assert len(enqueued) == 1
    assert sorted(enqueued[0]) == sorted(str(task.id) for task in tasks)
    assert all(task.status == "completed" for task in tasks)


@pytest.mark.asyncio
async def test_late_child_bridge_repairs_parent_checkpoint_before_enqueue(monkeypatch, tmp_path):
    from agent import subtask_bridge

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    parent = SimpleNamespace(
        id=parent_id,
        user_id=uuid.uuid4(),
        status="idle",
        agent_id="assistant",
        model_id="test-model",
    )
    child = SimpleNamespace(id=child_id, status="idle")
    task = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=parent_id,
        task_kind="child_session",
        blocking_mode="blocking",
        status="running",
        child_session_id=child_id,
        title="Batch",
        result_ref=None,
    )
    fake_db = _FakeDb(parent=parent, children=[child], tasks=[task])
    order = []

    monkeypatch.setattr(subtask_bridge, "AsyncSessionLocal", lambda: fake_db)
    monkeypatch.setattr(subtask_bridge, "_parent_session_dir", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr(subtask_bridge, "_can_append_parent_bridge", AsyncMock(return_value=True))

    async def fake_repair(*args, **kwargs):
        order.append("repair")
        return {"provider_payload_preflight_ok": True}

    async def fake_create_message(*args, **kwargs):
        order.append("message")
        return SimpleNamespace()

    async def fake_enqueue(*args, **kwargs):
        order.append("enqueue")
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        subtask_bridge,
        "repair_parent_checkpoint_before_subtask_continuation",
        fake_repair,
    )
    monkeypatch.setattr("session.service.create_message", fake_create_message)
    monkeypatch.setattr(subtask_bridge, "_enqueue_parent_continuation", fake_enqueue)

    result = await subtask_bridge.bridge_reconcilable_child_tasks(parent_id)

    assert result.enqueued_run_id is not None
    assert order == ["repair", "message", "enqueue"]


@pytest.mark.asyncio
async def test_late_child_bridge_does_not_enqueue_when_checkpoint_repair_fails(monkeypatch, tmp_path):
    from agent import subtask_bridge

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    parent = SimpleNamespace(
        id=parent_id,
        user_id=uuid.uuid4(),
        status="idle",
        agent_id="assistant",
        model_id="test-model",
    )
    child = SimpleNamespace(id=child_id, status="idle")
    task = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=parent_id,
        task_kind="child_session",
        blocking_mode="blocking",
        status="running",
        child_session_id=child_id,
        title="Batch",
        result_ref=None,
    )
    fake_db = _FakeDb(parent=parent, children=[child], tasks=[task])
    enqueued = []

    monkeypatch.setattr(subtask_bridge, "AsyncSessionLocal", lambda: fake_db)
    monkeypatch.setattr(subtask_bridge, "_parent_session_dir", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr(subtask_bridge, "_can_append_parent_bridge", AsyncMock(return_value=True))
    monkeypatch.setattr(
        subtask_bridge,
        "repair_parent_checkpoint_before_subtask_continuation",
        AsyncMock(side_effect=RuntimeError("checkpoint still dirty")),
    )

    async def fake_enqueue(*args, **kwargs):
        enqueued.append(True)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(subtask_bridge, "_enqueue_parent_continuation", fake_enqueue)

    result = await subtask_bridge.bridge_reconcilable_child_tasks(parent_id)

    assert result.enqueued_run_id is None
    assert result.provider_payload_preflight_ok is False
    assert result.checkpoint_repair_error == "checkpoint still dirty"
    assert result.delayed_task_ids == [str(task.id)]
    assert enqueued == []
    assert fake_db.rolled_back is True
    assert task.status == "running"


@pytest.mark.asyncio
async def test_duplicate_bridge_is_idempotent_when_task_already_completed(monkeypatch):
    from agent import subtask_bridge

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    task_id = uuid.uuid4()
    parent = SimpleNamespace(
        id=parent_id,
        user_id=uuid.uuid4(),
        status="idle",
        agent_id="assistant",
        model_id="test-model",
    )
    child = SimpleNamespace(id=child_id, status="idle")
    task = SimpleNamespace(
        id=task_id,
        session_id=parent_id,
        task_kind="child_session",
        blocking_mode="blocking",
        status="completed",
        child_session_id=child_id,
        title="Batch",
        result_ref=f".agentd/tasks/{task_id}/result.json",
    )
    parent_message = SimpleNamespace(
        role="assistant",
        parts=[{
            "type": "subtask_result",
            "task_id": str(task_id),
            "child_session_id": str(child_id),
        }],
    )
    fake_db = _FakeDb(
        parent=parent,
        children=[child],
        tasks=[task],
        parent_messages=[parent_message],
    )
    monkeypatch.setattr(subtask_bridge, "AsyncSessionLocal", lambda: fake_db)

    result = await subtask_bridge.bridge_reconcilable_child_tasks(parent_id)

    assert result.bridged_task_ids == []
    assert result.completed_task_ids == []
    assert result.enqueued_run_id is None
    assert result.skipped_reason == "no_active_child_tasks"


@pytest.mark.asyncio
async def test_deprecated_graph_compact_defers_open_tool_group():
    from agent.nodes import compact_context

    state = {
        "session_id": "s1",
        "messages": [
            AIMessage(content=f"msg {idx}") for idx in range(7)
        ] + [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_a", "name": "list_dir", "args": {}},
                    {"id": "call_b", "name": "bash", "args": {}},
                ],
            ),
            ToolMessage(content="ok", tool_call_id="call_a", name="list_dir"),
        ],
        "model_id": "test-model",
        "token_usage": {"input": 0, "output": 0, "total": 0},
    }

    result = await compact_context(state)

    assert result == {}
