"""v0.4.7 Phase C /retry active recovery truth contract tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, *execute_values):
        self._execute_values = list(execute_values)

    async def execute(self, stmt):
        return _Result(self._execute_values.pop(0))

    async def flush(self):
        return None

    async def commit(self):
        return None


def _session(session_id, user_id):
    return SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status="idle",
    )


@pytest.mark.asyncio
async def test_retry_rejects_stale_historical_failed_run():
    from session.router import retry_session_model_continuation

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id)
    session = _session(session_id, user_id)
    failed_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="failed",
        error="APITimeoutError: timeout",
        diagnostics={"recovery_envelope": {"category": "provider_transient"}},
    )
    completed_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        error=None,
        diagnostics={},
        payload={},
    )

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
        patch("agent.projection_consistency.repair_session_projection_ahead", new=AsyncMock(return_value=(None, None))),
        patch("session.router._inspect_open_hitl_recovery", new=AsyncMock(return_value=SimpleNamespace(action="none", decisions=None, checkpoint_state=None))),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch("agent.scheduler.enqueue_continue", new=AsyncMock()) as enqueue_continue,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await retry_session_model_continuation(
                session_id,
                db=_FakeDb(completed_run, failed_run),  # type: ignore[arg-type]
                current_user=current_user,  # type: ignore[arg-type]
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "NOT_RETRYABLE"
    assert exc_info.value.detail["reason"] == "no_failed_run"
    enqueue_continue.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_accepts_latest_active_failed_run(monkeypatch):
    from agent.checkpoint_state import CheckpointState, CheckpointStateKind
    from session.router import retry_session_model_continuation

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id)
    session = _session(session_id, user_id)
    failed_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="failed",
        error="APITimeoutError: timeout",
        diagnostics={"provider_error_category": "provider_transient"},
        payload={},
    )
    checkpoint = CheckpointState(
        state_kind=CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT,
        is_provider_payload_ready=True,
        is_recoverable=True,
        requires_human_input=False,
        message_count=3,
        next_nodes=["model"],
    )
    continue_run = SimpleNamespace(id=uuid.uuid4())

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
        patch("agent.projection_consistency.repair_session_projection_ahead", new=AsyncMock(return_value=(None, None))),
        patch("session.router._inspect_open_hitl_recovery", new=AsyncMock(return_value=SimpleNamespace(action="none", decisions=None, checkpoint_state=None))),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=checkpoint)),
        patch("agent.scheduler.enqueue_continue", new=AsyncMock(return_value=continue_run)) as enqueue_continue,
        patch("session.router.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        response = await retry_session_model_continuation(
            session_id,
            db=_FakeDb(failed_run, failed_run),  # type: ignore[arg-type]
            current_user=current_user,  # type: ignore[arg-type]
        )

    assert response["data"]["run_id"] == str(continue_run.id)
    enqueue_continue.assert_awaited_once()
    update_status.assert_awaited_once()
