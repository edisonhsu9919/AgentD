"""v0.4.7 Phase C reactive context compact recovery tests."""

from __future__ import annotations

import uuid
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.auto_recovery import attempt_auto_recovery
from agent.auto_recovery import AutoRecoveryResult
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
async def test_context_overflow_reactive_compact_enqueues_narrow_continue():
    session_id = uuid.uuid4()
    failed_run = SimpleNamespace(id=uuid.uuid4(), diagnostics={})
    continue_run = SimpleNamespace(id=uuid.uuid4())
    envelope = RuntimeErrorClassifier.classify_error_text(
        "Context size has been exceeded.",
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
        patch("agent.auto_recovery._run_reactive_compact", new=AsyncMock(return_value={"compacted": True})),
        patch("agent.auto_recovery._recovery_decision", new=AsyncMock(return_value=decision)) as recovery_decision,
        patch("agent.auto_recovery.scheduler.enqueue_continue", new=AsyncMock(return_value=continue_run)) as enqueue,
        patch("agent.auto_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.auto_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        result = await attempt_auto_recovery(
            _FakeDb(failed_run),  # type: ignore[arg-type]
            session_id=session_id,
            failed_run_id=failed_run.id,
            envelope=envelope,
        )

    assert result.attempted is True
    assert result.enqueued is True
    assert result.strategy == "reactive_compact_then_continue"
    payload = enqueue.await_args.kwargs["payload"]
    assert payload["auto_recovery"]["strategy"] == "reactive_compact_then_continue"
    assert payload["auto_recovery"]["attempted"] == 1
    assert recovery_decision.await_args.kwargs["recovery_diagnostics"] == {
        "reactive_compact_succeeded": True,
        "compact_result": {"compacted": True},
    }
    diagnostics = update_diag.await_args_list[0].args[2]
    assert diagnostics["recovery_envelope"]["auto_recovery"]["attempted"] == 1
    update_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_overflow_compact_failure_stays_recoverable_not_enqueued():
    session_id = uuid.uuid4()
    failed_run = SimpleNamespace(id=uuid.uuid4(), diagnostics={})
    envelope = RuntimeErrorClassifier.classify_error_text(
        "maximum context length exceeded",
        run_type="start",
    )

    with (
        patch(
            "agent.auto_recovery._run_reactive_compact",
            new=AsyncMock(return_value={"compacted": False, "reason": "summary_generation_failed"}),
        ),
        patch("agent.auto_recovery.scheduler.enqueue_continue", new=AsyncMock()) as enqueue,
        patch("agent.auto_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.auto_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        result = await attempt_auto_recovery(
            _FakeDb(failed_run),  # type: ignore[arg-type]
            session_id=session_id,
            failed_run_id=failed_run.id,
            envelope=envelope,
        )

    assert result.attempted is True
    assert result.enqueued is False
    assert result.reason == "summary_generation_failed"
    enqueue.assert_not_awaited()
    update_status.assert_not_awaited()
    diagnostics = update_diag.await_args_list[-1].args[2]
    assert diagnostics["recovery_envelope"]["next_action"] == "recover"
    assert diagnostics["recovery_envelope"]["auto_recovery"]["last_attempt_error"] == "summary_generation_failed"


@pytest.mark.asyncio
async def test_context_overflow_compact_exception_is_persisted_as_attempt_failure():
    session_id = uuid.uuid4()
    failed_run = SimpleNamespace(id=uuid.uuid4(), diagnostics={})
    envelope = RuntimeErrorClassifier.classify_error_text(
        "maximum context length exceeded",
        run_type="start",
    )

    with (
        patch(
            "agent.auto_recovery._run_reactive_compact",
            new=AsyncMock(side_effect=RuntimeError("compact exploded")),
        ),
        patch("agent.auto_recovery.scheduler.enqueue_continue", new=AsyncMock()) as enqueue,
        patch("agent.auto_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.auto_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        result = await attempt_auto_recovery(
            _FakeDb(failed_run),  # type: ignore[arg-type]
            session_id=session_id,
            failed_run_id=failed_run.id,
            envelope=envelope,
        )

    assert result.attempted is True
    assert result.enqueued is False
    assert result.reason == "reactive_compact_exception:RuntimeError"
    enqueue.assert_not_awaited()
    update_status.assert_not_awaited()
    diagnostics = update_diag.await_args_list[-1].args[2]
    auto = diagnostics["recovery_envelope"]["auto_recovery"]
    assert auto["attempted"] == 1
    assert auto["last_attempt_error"] == "reactive_compact_exception:RuntimeError"
    assert diagnostics["recovery_envelope"]["next_action"] == "recover"


@pytest.mark.asyncio
async def test_context_overflow_compact_timeout_stays_recoverable_not_running():
    session_id = uuid.uuid4()
    failed_run = SimpleNamespace(id=uuid.uuid4(), diagnostics={})
    envelope = RuntimeErrorClassifier.classify_error_text(
        "maximum context length exceeded",
        run_type="start",
    )

    async def _slow_compact(*args, **kwargs):
        await asyncio.sleep(1)
        return {"compacted": True}

    with (
        patch("agent.auto_recovery.REACTIVE_COMPACT_TIMEOUT_SECONDS", 0.01),
        patch("agent.auto_recovery._run_reactive_compact", new=AsyncMock(side_effect=_slow_compact)),
        patch("agent.auto_recovery.scheduler.enqueue_continue", new=AsyncMock()) as enqueue,
        patch("agent.auto_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.auto_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        result = await attempt_auto_recovery(
            _FakeDb(failed_run),  # type: ignore[arg-type]
            session_id=session_id,
            failed_run_id=failed_run.id,
            envelope=envelope,
        )

    assert result.attempted is True
    assert result.enqueued is False
    assert result.reason == "reactive_compact_timeout"
    enqueue.assert_not_awaited()
    update_status.assert_not_awaited()
    diagnostics = update_diag.await_args_list[-1].args[2]
    auto = diagnostics["recovery_envelope"]["auto_recovery"]
    assert auto["last_attempt_error"] == "reactive_compact_timeout"
    assert diagnostics["recovery_envelope"]["next_action"] == "recover"


@pytest.mark.asyncio
async def test_worker_commits_failed_overflow_before_reactive_compact_recovery():
    from agent.worker import AgentWorker

    run_id = uuid.uuid4()
    session_id = str(uuid.uuid4())
    overflow = RuntimeError("maximum context length exceeded")
    envelope = RuntimeErrorClassifier.classify_error_text(
        "maximum context length exceeded",
        run_type="start",
    )
    events: list[str] = []

    worker = AgentWorker(worker_id="phase-v047-reactive")
    worker._record_run_start_seq = AsyncMock()
    worker._execute_start = AsyncMock(side_effect=overflow)
    worker._bridge_child_failure = AsyncMock()
    worker._publish = AsyncMock()

    first_db = AsyncMock()
    first_db.commit = AsyncMock(side_effect=lambda: events.append("failure_committed"))
    first_ctx = AsyncMock()
    first_ctx.__aenter__.return_value = first_db
    first_ctx.__aexit__.return_value = False

    second_db = AsyncMock()
    second_db.commit = AsyncMock(side_effect=lambda: events.append("recovery_committed"))
    second_ctx = AsyncMock()
    second_ctx.__aenter__.return_value = second_db
    second_ctx.__aexit__.return_value = False

    async def _attempt(*args, **kwargs):
        events.append("auto_recovery_started")
        return AutoRecoveryResult(
            attempted=True,
            enqueued=True,
            run_id=uuid.uuid4(),
            strategy="reactive_compact_then_continue",
            reason="reactive_compact_continue_enqueued",
        )

    with (
        patch("agent.worker.AsyncSessionLocal", side_effect=[first_ctx, second_ctx]),
        patch("agent.runtime_recovery.finalize_run_failure", new=AsyncMock(return_value=envelope)),
        patch("agent.auto_recovery.attempt_auto_recovery", new=AsyncMock(side_effect=_attempt)),
    ):
        await worker._execute_run(run_id, session_id, "start", {})

    assert events == [
        "failure_committed",
        "auto_recovery_started",
        "recovery_committed",
    ]
