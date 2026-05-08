"""v0.4.7 Phase E repair API tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_doctor import DoctorAction, DoctorReport


@pytest.mark.asyncio
async def test_session_doctor_endpoint_runs_doctor_and_publishes_repair():
    from session.router import doctor_session_runtime

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(id=session_id, user_id=user_id, status="error")
    report = DoctorReport(
        session_id=session_id,
        dry_run=False,
        lock_acquired=True,
        actions=[
            DoctorAction(
                action="release_recoverable_error_session",
                applied=True,
                reason="recoverable_envelope",
            )
        ],
    )
    db = AsyncMock()

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
        patch("agent.session_doctor.run_session_doctor", new=AsyncMock(return_value=report)) as doctor,
        patch("core.events.event_bus.publish", new=AsyncMock()) as publish,
    ):
        response = await doctor_session_runtime(
            session_id,
            dry_run=False,
            db=db,  # type: ignore[arg-type]
            current_user=SimpleNamespace(id=user_id),  # type: ignore[arg-type]
        )

    doctor.assert_awaited_once()
    db.commit.assert_awaited_once()
    publish.assert_awaited_once()
    assert publish.await_args.args[1]["event"] == "session_doctor_repaired"
    assert response["data"]["repaired"] is True
    assert response["data"]["actions"][0]["action"] == "release_recoverable_error_session"
