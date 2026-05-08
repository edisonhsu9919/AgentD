"""v0.4.7 Phase D auto-recovery resolution tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from session.router import _active_recovery_run


class _FakeDb:
    def __init__(self, run):
        self.run = run

    async def get(self, model, run_id):
        if run_id == self.run.id:
            return self.run
        return None


def test_completed_auto_recovery_run_clears_active_recovery():
    source_run_id = uuid.uuid4()
    failed_run = SimpleNamespace(
        id=source_run_id,
        status="failed",
        error="APITimeoutError: timeout",
        diagnostics={"recovery_envelope": {"category": "provider_transient"}},
    )
    completed_recovery_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        error=None,
        diagnostics={},
        payload={
            "auto_recovery": {
                "source_run_id": str(source_run_id),
                "strategy": "narrow_continue_retry",
            }
        },
    )

    assert _active_recovery_run(
        SimpleNamespace(status="idle"),
        completed_recovery_run,
        failed_run,
    ) is None


def test_running_auto_recovery_run_keeps_source_recovery_active():
    source_run_id = uuid.uuid4()
    failed_run = SimpleNamespace(
        id=source_run_id,
        status="failed",
        error="APITimeoutError: timeout",
        diagnostics={"recovery_envelope": {"category": "provider_transient"}},
    )
    running_recovery_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        error=None,
        diagnostics={},
        payload={"auto_recovery": {"source_run_id": str(source_run_id)}},
    )

    assert _active_recovery_run(
        SimpleNamespace(status="queued"),
        running_recovery_run,
        failed_run,
    ) is failed_run


@pytest.mark.asyncio
async def test_successful_auto_recovery_marks_source_envelope_resolved():
    from agent.runtime_recovery import mark_recovery_resolved

    source_run_id = uuid.uuid4()
    resolved_by = uuid.uuid4()
    source_run = SimpleNamespace(
        id=source_run_id,
        diagnostics={
            "recovery_unresolved": True,
            "recovery_envelope": {"category": "provider_transient"},
        },
    )

    with patch("agent.runtime_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag:
        await mark_recovery_resolved(
            _FakeDb(source_run),  # type: ignore[arg-type]
            source_run_id=source_run_id,
            resolved_by_run_id=resolved_by,
            resolution="auto_recovery_completed",
        )

    update_diag.assert_awaited_once()
    diagnostics = update_diag.await_args.args[2]
    assert diagnostics["recovery_unresolved"] is False
    assert diagnostics["resolved_by_run_id"] == str(resolved_by)
    assert diagnostics["resolution"] == "auto_recovery_completed"
    assert diagnostics["resolved_at"]
    assert diagnostics["recovery_state"] == "none"
    assert diagnostics["recovery_envelope"]["next_action"] == "none"
    assert diagnostics["recovery_envelope"]["recovery_resolved"] is True
    assert diagnostics["recovery_envelope"]["auto_recovery"]["resolved_by_run_id"] == str(resolved_by)
