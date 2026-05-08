"""v0.4.7 Phase A session fail-soft finalizer tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.runtime_error_classifier import RuntimeErrorClassifier, session_status_for_envelope
from agent.runtime_recovery import finalize_run_failure


class _FakeDb:
    def __init__(self):
        self.run = SimpleNamespace(diagnostics={"existing": True})

    async def get(self, model, run_id):
        return self.run


@pytest.mark.asyncio
async def test_finalize_run_failure_releases_recoverable_session_to_idle():
    db = _FakeDb()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    with (
        patch("agent.runtime_recovery.scheduler.mark_failed", new=AsyncMock()) as mark_failed,
        patch("agent.runtime_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.runtime_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        envelope = await finalize_run_failure(
            db,  # type: ignore[arg-type]
            session_id=session_id,
            run_id=run_id,
            exc=TimeoutError("Request timed out."),
            run_type="start",
        )

    assert envelope.category == "provider_transient"
    assert envelope.severity == "recoverable"
    mark_failed.assert_awaited_once()
    update_diag.assert_awaited_once()
    diagnostics = update_diag.await_args.args[2]
    assert diagnostics["existing"] is True
    assert diagnostics["recovery_state"] == "recoverable"
    assert diagnostics["recovery_envelope"]["category"] == "provider_transient"
    update_status.assert_awaited_once_with(db, session_id, "idle")


@pytest.mark.asyncio
async def test_finalize_run_failure_sets_terminal_session_error():
    db = _FakeDb()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()

    with (
        patch("agent.runtime_recovery.scheduler.mark_failed", new=AsyncMock()),
        patch("agent.runtime_recovery.scheduler.update_diagnostics", new=AsyncMock()),
        patch("agent.runtime_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        envelope = await finalize_run_failure(
            db,  # type: ignore[arg-type]
            session_id=session_id,
            run_id=run_id,
            exc=AssertionError("internal invariant violated"),
            run_type="start",
        )

    assert envelope.category == "internal_invariant_violation"
    assert envelope.severity == "terminal"
    update_status.assert_awaited_once_with(db, session_id, "error")


def test_session_status_for_recovery_envelope_contract():
    recoverable = RuntimeErrorClassifier.classify_error_text("No generations found in stream")
    terminal = RuntimeErrorClassifier.classify_error_text("AssertionError: invariant")

    assert session_status_for_envelope(recoverable) == "idle"
    assert session_status_for_envelope(terminal) == "error"
