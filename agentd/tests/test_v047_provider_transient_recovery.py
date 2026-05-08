"""v0.4.7 Phase C provider transient bounded auto recovery tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.auto_recovery import attempt_auto_recovery
from agent.recovery_policy import RecoveryDecisionKind
from agent.runtime_error_classifier import RuntimeErrorClassifier


class _FakeDb:
    def __init__(self, run):
        self.run = run

    async def get(self, model, item_id):
        if item_id == self.run.id:
            return self.run
        return None


@pytest.mark.asyncio
async def test_provider_transient_auto_retry_enqueues_one_continue_run():
    session_id = uuid.uuid4()
    failed_run = SimpleNamespace(id=uuid.uuid4(), diagnostics={})
    retry_run = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDb(failed_run)
    envelope = RuntimeErrorClassifier.classify_error_text(
        "APITimeoutError: request timed out",
        run_type="start",
    )
    decision = SimpleNamespace(
        kind=RecoveryDecisionKind.CONTINUE_MODEL,
        allowed=True,
        target_payload={"mode": "retry_model_node", "source_run_id": str(failed_run.id)},
        checkpoint_state_kind="next_model_after_tool_result",
        reason="provider_failure_after_closed_tool_result",
    )

    with (
        patch("agent.auto_recovery._recovery_decision", new=AsyncMock(return_value=decision)),
        patch("agent.auto_recovery.scheduler.enqueue_continue", new=AsyncMock(return_value=retry_run)) as enqueue,
        patch("agent.auto_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.auto_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        result = await attempt_auto_recovery(
            db,  # type: ignore[arg-type]
            session_id=session_id,
            failed_run_id=failed_run.id,
            envelope=envelope,
        )

    assert result.attempted is True
    assert result.enqueued is True
    assert result.strategy == "narrow_continue_retry"
    enqueue.assert_awaited_once()
    payload = enqueue.await_args.kwargs["payload"]
    assert payload["mode"] == "retry_model_node"
    assert payload["auto_recovery"]["attempted"] == 1
    assert payload["auto_recovery"]["source_run_id"] == str(failed_run.id)
    diagnostics = update_diag.await_args.args[2]
    assert diagnostics["recovery_envelope"]["auto_recovery"]["attempted"] == 1
    assert diagnostics["recovery_envelope"]["next_action"] == "auto_recovering"
    update_status.assert_awaited_once_with(db, session_id, "queued")


@pytest.mark.asyncio
async def test_provider_transient_auto_retry_stops_after_attempt_budget():
    session_id = uuid.uuid4()
    failed_run_id = uuid.uuid4()
    envelope = RuntimeErrorClassifier.classify_error_text(
        "APITimeoutError: request timed out",
        run_type="continue",
        context={"auto_recovery_attempted": 1},
    )

    result = await attempt_auto_recovery(
        _FakeDb(SimpleNamespace(id=failed_run_id, diagnostics={})),  # type: ignore[arg-type]
        session_id=session_id,
        failed_run_id=failed_run_id,
        envelope=envelope,
    )

    assert result.attempted is False
    assert result.enqueued is False
    assert result.reason == "attempt_budget_exhausted"
