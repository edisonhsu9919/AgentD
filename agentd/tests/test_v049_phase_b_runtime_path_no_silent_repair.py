"""v0.4.9 Phase B: runtime hot paths must not run silent projection repair.

Phase B moves DB-projection repair off the runtime path (worker terminal,
prompt ingress, retry endpoint) and onto the explicit doctor entry point.
These tests pin that contract: with the default rollback flag off, the hot
paths must not call inspect_db_checkpoint_projection /
repair_db_projection_ahead.

Test isolation note: the retry route is short-circuited via session.status in
{"running", "waiting", "queued", "subtask_waiting"} so we hit the early 409
right after the projection-repair gate. This avoids running through
``_active_recovery_run`` / ``_inspect_open_hitl_recovery`` /
``_load_checkpoint_state`` (which would otherwise trigger a real
``build_agent`` and a model_configs DB lookup, polluting the test).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def _make_session(user_id: uuid.UUID, status: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status=status,
        agent_id=None,
        model_id=None,
        parent_id=None,
    )


@pytest.mark.asyncio
async def test_retry_endpoint_does_not_run_silent_projection_repair():
    """retry route must not silently call repair_session_projection_ahead.

    With the default flag off, the projection repair branch is gated; the
    route then trips the running-state 409 short-circuit, so we never reach
    deeper machinery (no build_agent, no model_configs lookup).
    """
    from session.router import retry_session_model_continuation

    user = SimpleNamespace(id=uuid.uuid4(), workspace="/tmp/u")
    session = _make_session(user.id, status="running")

    with (
        patch(
            "session.router.session_svc.get_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "agent.projection_consistency.repair_session_projection_ahead",
            new=AsyncMock(),
        ) as repair_mock,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await retry_session_model_continuation(
                session_id=session.id,
                db=AsyncMock(),
                current_user=user,
            )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "CONFLICT"
    repair_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_endpoint_runs_projection_repair_under_legacy_flag(monkeypatch):
    """Behind the rollback flag, retry route still runs the legacy repair path."""
    from core.config import settings as _settings
    from session.router import retry_session_model_continuation

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)

    user = SimpleNamespace(id=uuid.uuid4(), workspace="/tmp/u")
    session = _make_session(user.id, status="running")

    repair_mock = AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(repaired=False)))
    with (
        patch(
            "session.router.session_svc.get_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "agent.projection_consistency.repair_session_projection_ahead",
            new=repair_mock,
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await retry_session_model_continuation(
                session_id=session.id,
                db=AsyncMock(),
                current_user=user,
            )

    # Status 409 from running-state short circuit; but repair_mock should have
    # fired before that gate.
    assert excinfo.value.status_code == 409
    repair_mock.assert_awaited_once()
