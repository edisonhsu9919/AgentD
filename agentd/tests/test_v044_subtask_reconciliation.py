"""v0.4.4 child task reconciliation tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, *, parent, user, child, task, latest_run, latest_message):
        self.parent = parent
        self.user = user
        self.children = child if isinstance(child, list) else [child]
        self.tasks = task if isinstance(task, list) else [task]
        self.latest_runs = (
            latest_run
            if isinstance(latest_run, dict)
            else {self.children[0].id: latest_run}
        )
        self.latest_messages = (
            latest_message
            if isinstance(latest_message, dict)
            else {self.children[0].id: latest_message}
        )
        self._run_index = 0
        self._message_index = 0

    async def get(self, model, item_id):
        name = getattr(model, "__name__", "")
        if name == "Session" and item_id == self.parent.id:
            return self.parent
        for child in self.children:
            if name == "Session" and item_id == child.id:
                return child
        if name == "User" and item_id == self.user.id:
            return self.user
        return None

    async def execute(self, stmt):
        entity = (getattr(stmt, "column_descriptions", None) or [{}])[0].get("entity")
        name = getattr(entity, "__name__", "")
        if name == "SessionTask":
            return _Result(self.tasks)
        if name == "AgentRun":
            child_id = self.tasks[self._run_index].child_session_id
            self._run_index += 1
            return _Result([self.latest_runs[child_id]])
        if name == "Message":
            child_id = self.tasks[self._message_index].child_session_id
            self._message_index += 1
            return _Result([self.latest_messages[child_id]])
        return _Result([])


@pytest.mark.asyncio
async def test_reconcile_completed_child_task_marks_stale_running_complete(tmp_path):
    from agent.subtask_reconciliation import reconcile_completed_child_tasks

    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()

    parent = SimpleNamespace(id=parent_id, user_id=user_id)
    user = SimpleNamespace(id=user_id, workspace=str(tmp_path))
    child = SimpleNamespace(id=child_id, status="idle")
    task = SimpleNamespace(
        id=task_id,
        session_id=parent_id,
        task_kind="child_session",
        status="running",
        child_session_id=child_id,
        result_ref=None,
    )
    latest_run = SimpleNamespace(status="completed", error=None)
    latest_message = SimpleNamespace(
        parts=[{"type": "text", "content": "child done"}],
    )

    db = _FakeDb(
        parent=parent,
        user=user,
        child=child,
        task=task,
        latest_run=latest_run,
        latest_message=latest_message,
    )

    result = await reconcile_completed_child_tasks(db, parent_id)

    assert result.reconciled_count == 1
    assert task.status == "completed"
    assert task.result_ref == f".agentd/tasks/{task_id}/result.json"
    assert (
        tmp_path
        / "sessions"
        / str(parent_id)
        / ".agentd/tasks"
        / str(task_id)
        / "result.json"
    ).exists()


@pytest.mark.asyncio
async def test_reconcile_completed_child_tasks_marks_all_stale_running_complete(tmp_path):
    from agent.subtask_reconciliation import reconcile_completed_child_tasks

    parent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent = SimpleNamespace(id=parent_id, user_id=user_id)
    user = SimpleNamespace(id=user_id, workspace=str(tmp_path))
    children = [
        SimpleNamespace(id=uuid.uuid4(), status="idle")
        for _ in range(3)
    ]
    tasks = [
        SimpleNamespace(
            id=uuid.uuid4(),
            session_id=parent_id,
            task_kind="child_session",
            status="running",
            child_session_id=child.id,
            result_ref=None,
        )
        for child in children
    ]
    latest_runs = {
        child.id: SimpleNamespace(status="completed", error=None)
        for child in children
    }
    latest_messages = {
        child.id: SimpleNamespace(parts=[{"type": "text", "content": f"done {idx}"}])
        for idx, child in enumerate(children)
    }
    db = _FakeDb(
        parent=parent,
        user=user,
        child=children,
        task=tasks,
        latest_run=latest_runs,
        latest_message=latest_messages,
    )

    result = await reconcile_completed_child_tasks(db, parent_id)

    assert result.reconciled_count == 3
    assert all(task.status == "completed" for task in tasks)
    assert all(task.result_ref == f".agentd/tasks/{task.id}/result.json" for task in tasks)
