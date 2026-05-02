"""Worker terminal runtime integrity guard tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.runtime_integrity import RuntimeGateAction, RuntimeGateDecision, RuntimeIntegrityError


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeDb:
    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_worker_refuses_completed_run_when_integrity_gate_fails():
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v044-integrity")
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
        reason="db_tail_open_tool_call",
        open_tool_call_ids=["call_edit"],
        checkpoint_state_kind=None,
        can_accept_user_prompt=False,
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="idle")),
        ),
        pytest.raises(RuntimeIntegrityError) as excinfo,
    ):
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    assert excinfo.value.decision.reason == "db_tail_open_tool_call"
    assert excinfo.value.decision.open_tool_call_ids == ["call_edit"]


@pytest.mark.asyncio
async def test_worker_allows_waiting_gate_and_repairs_idle_status():
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v044-integrity")
    worker._publish = AsyncMock()
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.ENTER_WAITING,
        reason="hitl_open_tool_call_waiting",
        open_tool_call_ids=["call_write"],
        checkpoint_state_kind="hitl_open_tool_call",
        requires_human_input=True,
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="idle")),
        ),
        patch("agent.worker.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    update_status.assert_awaited_once()
    worker._publish.assert_awaited_once_with(
        str(session_id),
        {"event": "status_change", "status": "waiting"},
    )


@pytest.mark.asyncio
async def test_worker_preserves_subtask_waiting_terminal_state():
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v044-subtask")
    worker._publish = AsyncMock()
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.ENTER_SUBTASK_WAITING,
        reason="session_subtask_waiting",
        checkpoint_state_kind=None,
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="subtask_waiting")),
        ),
        patch("agent.worker.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    update_status.assert_not_awaited()
    worker._publish.assert_not_awaited()


def test_parent_child_bridge_uses_task_truth_and_accepts_late_idle_parent():
    from agent.worker import _parent_can_accept_child_bridge

    task = SimpleNamespace(
        task_kind="child_session",
        blocking_mode="blocking",
        status="running",
        child_session_id=uuid.uuid4(),
    )

    assert _parent_can_accept_child_bridge(SimpleNamespace(status="idle"), task)
    assert _parent_can_accept_child_bridge(SimpleNamespace(status="subtask_waiting"), task)
    assert _parent_can_accept_child_bridge(SimpleNamespace(status="waiting"), task)
    assert _parent_can_accept_child_bridge(SimpleNamespace(status="queued"), task)
    assert _parent_can_accept_child_bridge(SimpleNamespace(status="running"), task)
    assert not _parent_can_accept_child_bridge(SimpleNamespace(status="error"), task)
    assert not _parent_can_accept_child_bridge(SimpleNamespace(status="waiting"), None)
    assert not _parent_can_accept_child_bridge(
        SimpleNamespace(status="waiting"),
        SimpleNamespace(
            task_kind="process",
            blocking_mode="detached",
            status="running",
            child_session_id=None,
        ),
    )


@pytest.mark.asyncio
async def test_parent_subtask_result_lookup_is_idempotency_guard():
    from agent.worker import _parent_has_subtask_result

    task_id = str(uuid.uuid4())
    child_session_id = str(uuid.uuid4())

    class _ScalarResult:
        def all(self):
            return [
                SimpleNamespace(parts=[{
                    "type": "subtask_result",
                    "task_id": task_id,
                    "child_session_id": child_session_id,
                }])
            ]

    class _Result:
        def scalars(self):
            return _ScalarResult()

    db = SimpleNamespace(execute=AsyncMock(return_value=_Result()))

    assert await _parent_has_subtask_result(
        db,
        uuid.uuid4(),
        task_id,
        child_session_id,
    )
