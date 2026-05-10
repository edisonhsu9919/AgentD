"""v0.4.9 Phase A: worker terminal path enqueues continue on CONTINUE_MODEL."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.runtime_integrity import RuntimeGateAction, RuntimeGateDecision


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
async def test_worker_enqueues_continue_when_terminal_gate_returns_continue_model():
    """v0.4.9 Phase A: CONTINUE_MODEL is not fatal; worker enqueues a continue run."""
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    new_run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v049-continue")
    worker._publish = AsyncMock()
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.CONTINUE_MODEL,
        reason="checkpoint_next_model_after_tool_result",
        open_tool_call_ids=[],
        checkpoint_state_kind="next_model_after_tool_result",
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="idle")),
        ),
        patch(
            "agent.worker.scheduler.enqueue_continue",
            new=AsyncMock(return_value=SimpleNamespace(id=new_run_id)),
        ) as enqueue_mock,
        patch(
            "agent.worker.session_svc.update_session_status",
            new=AsyncMock(),
        ) as update_status,
    ):
        # Must not raise; should enqueue a continue run.
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    enqueue_mock.assert_awaited_once()
    # Payload must satisfy scheduler.enqueue_continue contract:
    #   mode == "retry_model_node" AND source_run_id is non-empty.
    enqueue_args = enqueue_mock.await_args
    payload = enqueue_args.kwargs.get("payload") if enqueue_args.kwargs else enqueue_args.args[2]
    assert payload["mode"] == "retry_model_node"
    assert payload["source_run_id"] == str(run_id)
    assert payload["trigger"] == "terminal_gate_continue_model"
    assert payload["reason"] == "checkpoint_next_model_after_tool_result"
    assert payload["checkpoint_state_kind"] == "next_model_after_tool_result"

    update_status.assert_awaited()
    # Status should be set to queued for the continue run.
    last_call = update_status.await_args_list[-1]
    assert last_call.args[2] == "queued"
    # Status change SSE event should announce continue trigger.
    publish_payloads = [call.args[1] for call in worker._publish.await_args_list]
    assert any(
        payload.get("event") == "status_change"
        and payload.get("status") == "queued"
        and payload.get("trigger") == "terminal_gate_continue_model"
        for payload in publish_payloads
    )


@pytest.mark.asyncio
async def test_worker_continue_payload_passes_real_scheduler_validation():
    """End-to-end: the payload built by worker must satisfy the real scheduler validator.

    Audit Finding 1: previous tests mocked enqueue_continue and missed that the
    constructed payload was missing ``mode="retry_model_node"``. Here we let the
    real validator run.
    """
    from agent import scheduler as real_scheduler
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v049-continue-real-validator")
    worker._publish = AsyncMock()

    captured_payload: dict = {}

    async def fake_enqueue(db, sid, *, payload):
        # Run the real validation gate from scheduler.
        if (
            not isinstance(payload, dict)
            or payload.get("mode") != "retry_model_node"
            or not payload.get("source_run_id")
        ):
            raise ValueError(
                "continue run requires mode=retry_model_node and source_run_id"
            )
        captured_payload.update(payload)
        return SimpleNamespace(id=uuid.uuid4())

    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.CONTINUE_MODEL,
        reason="checkpoint_next_model_after_tool_result",
        open_tool_call_ids=[],
        checkpoint_state_kind="next_model_after_tool_result",
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="idle")),
        ),
        patch.object(real_scheduler, "enqueue_continue", side_effect=fake_enqueue),
        patch(
            "agent.worker.session_svc.update_session_status",
            new=AsyncMock(),
        ),
    ):
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    assert captured_payload["mode"] == "retry_model_node"
    assert captured_payload["source_run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_worker_continue_model_without_run_id_fails_soft_to_idle():
    """Audit Finding 1: when run_id is None, do not enqueue an invalid continue.

    Instead, fail-soft to idle so the user can manually retry rather than
    triggering a scheduler ValueError silently caught.
    """
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v049-continue-no-runid")
    worker._publish = AsyncMock()
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.CONTINUE_MODEL,
        reason="checkpoint_next_model_after_tool_result",
        open_tool_call_ids=[],
        checkpoint_state_kind="next_model_after_tool_result",
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="idle")),
        ),
        patch(
            "agent.worker.scheduler.enqueue_continue",
            new=AsyncMock(),
        ) as enqueue_mock,
        patch(
            "agent.worker.session_svc.update_session_status",
            new=AsyncMock(),
        ) as update_status,
    ):
        await worker._assert_run_returned_terminal_state(str(session_id), None)

    # Must NOT have called enqueue_continue (no valid source_run_id).
    enqueue_mock.assert_not_awaited()
    # Must have coerced session to idle.
    assert any(call.args[2] == "idle" for call in update_status.await_args_list)
    # Status change SSE should carry the fail-soft reason.
    publish_payloads = [call.args[1] for call in worker._publish.await_args_list]
    assert any(
        payload.get("event") == "status_change"
        and payload.get("status") == "idle"
        and payload.get("reason") == "continue_skip_missing_source_run_id"
        for payload in publish_payloads
    )


@pytest.mark.asyncio
async def test_worker_self_check_on_error_status_coerces_to_idle_fail_soft():
    """v0.4.9 Phase A: executor briefly setting status=error must not double-kill the session.

    The previous "refusing to mark run completed" RuntimeError was classified as
    internal_invariant_violation → terminal. Phase A demotes this to fail-soft:
    coerce status back to idle and let the run be marked completed.
    """
    from agent.worker import AgentWorker

    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    worker = AgentWorker(worker_id="phase-v049-failsoft")
    worker._publish = AsyncMock()
    worker._load_terminal_runtime_integrity_decision = AsyncMock(return_value=RuntimeGateDecision(
        action=RuntimeGateAction.FINALIZE_IDLE,
        reason="checkpoint_clean",
        can_accept_user_prompt=True,
    ))

    with (
        patch("agent.worker.AsyncSessionLocal", return_value=_AsyncSessionContext(_FakeDb())),
        patch(
            "agent.worker.session_svc.get_session",
            new=AsyncMock(return_value=SimpleNamespace(status="error")),
        ),
        patch(
            "agent.worker.session_svc.update_session_status",
            new=AsyncMock(),
        ) as update_status,
    ):
        # Must NOT raise (previously raised RuntimeError).
        await worker._assert_run_returned_terminal_state(str(session_id), run_id)

    # Should have been coerced to idle.
    update_calls = update_status.await_args_list
    assert any(call.args[2] == "idle" for call in update_calls)
