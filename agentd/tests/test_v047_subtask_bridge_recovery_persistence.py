"""v0.4.7 Phase B subtask bridge recovery persistence tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.runtime_error_classifier import RuntimeErrorClassifier
from agent.runtime_recovery import persist_session_recovery_envelope


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, latest_run):
        self.latest_run = latest_run

    async def get(self, model, run_id):
        if self.latest_run and self.latest_run.id == run_id:
            return self.latest_run
        return None

    async def execute(self, stmt):
        return _FakeResult(self.latest_run)


@pytest.mark.asyncio
async def test_subtask_bridge_failure_persists_envelope_to_latest_parent_run():
    session_id = uuid.uuid4()
    latest_run = SimpleNamespace(
        id=uuid.uuid4(),
        diagnostics={"existing": True},
    )
    db = _FakeDb(latest_run)
    envelope = RuntimeErrorClassifier.classify_error_text(
        "Parent checkpoint tool adjacency remains invalid",
        run_type="subtask_bridge",
        context={
            "bridge_child_session_id": "child-1",
            "bridge_failure_without_run_id": True,
        },
    )

    with (
        patch("agent.runtime_recovery.scheduler.update_diagnostics", new=AsyncMock()) as update_diag,
        patch("agent.runtime_recovery.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        persisted_run_id = await persist_session_recovery_envelope(
            db,  # type: ignore[arg-type]
            session_id=session_id,
            envelope=envelope,
            extra_diagnostics={
                "source": "subtask",
                "bridge_child_session_id": "child-1",
                "bridge_failure_without_run_id": True,
            },
        )

    assert persisted_run_id == latest_run.id
    update_diag.assert_awaited_once()
    diagnostics = update_diag.await_args.args[2]
    assert diagnostics["existing"] is True
    assert diagnostics["recovery_unresolved"] is True
    assert diagnostics["recovery_scope"] == "session"
    assert diagnostics["source"] == "subtask"
    assert diagnostics["bridge_child_session_id"] == "child-1"
    assert diagnostics["bridge_failure_without_run_id"] is True
    assert diagnostics["recovery_envelope"]["category"] == "subtask_bridge_error"
    update_status.assert_awaited_once_with(db, session_id, "idle")
