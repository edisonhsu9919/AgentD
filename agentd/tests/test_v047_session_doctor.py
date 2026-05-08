"""v0.4.7 Phase E session doctor tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_doctor import run_session_doctor


class _FakeDb:
    pass


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _PostgresLockErrorDb:
    __module__ = "production.db"
    bind = _Bind()

    async def execute(self, *args, **kwargs):
        raise RuntimeError("advisory lock query failed")


def _session(status: str):
    return SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), status=status)


@pytest.mark.asyncio
async def test_doctor_releases_running_session_without_active_run():
    session = _session("running")
    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=True)),
        patch("agent.session_doctor._active_or_queued_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor._latest_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        report = await run_session_doctor(_FakeDb(), session=session)  # type: ignore[arg-type]

    assert report.repaired is True
    assert report.actions[0].action == "release_stale_active_status"
    update_status.assert_awaited_once_with(update_status.await_args.args[0], session.id, "idle")
    assert session.status == "idle"


@pytest.mark.asyncio
async def test_doctor_releases_waiting_without_pending_permission():
    session = _session("waiting")
    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=True)),
        patch("permission.service.count_pending_by_session", new=AsyncMock(return_value=0)),
        patch("agent.session_doctor._latest_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        report = await run_session_doctor(_FakeDb(), session=session)  # type: ignore[arg-type]

    assert report.repaired is True
    assert report.actions[0].action == "release_waiting_without_pending_permission"
    update_status.assert_awaited_once_with(update_status.await_args.args[0], session.id, "idle")


@pytest.mark.asyncio
async def test_doctor_releases_error_session_with_recoverable_envelope():
    session = _session("error")
    failed_run = SimpleNamespace(
        id=uuid.uuid4(),
        diagnostics={
            "recovery_envelope": {
                "category": "provider_transient",
                "severity": "recoverable",
                "next_action": "retry",
            }
        },
    )
    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=True)),
        patch("agent.session_doctor._latest_failed_run", new=AsyncMock(return_value=failed_run)),
        patch("agent.session_doctor._latest_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        report = await run_session_doctor(_FakeDb(), session=session)  # type: ignore[arg-type]

    assert report.repaired is True
    assert report.actions[0].action == "release_recoverable_error_session"
    assert report.actions[0].details["category"] == "provider_transient"
    update_status.assert_awaited_once_with(update_status.await_args.args[0], session.id, "idle")


@pytest.mark.asyncio
async def test_doctor_marks_completed_auto_recovery_resolved():
    session = _session("idle")
    source_run_id = uuid.uuid4()
    latest_run = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        error=None,
        payload={"auto_recovery": {"source_run_id": str(source_run_id)}},
    )
    source_run = SimpleNamespace(
        id=source_run_id,
        diagnostics={"recovery_unresolved": True, "recovery_envelope": {"category": "provider_transient"}},
    )
    db = AsyncMock()
    db.get.return_value = source_run

    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=True)),
        patch("agent.session_doctor._latest_run", new=AsyncMock(return_value=latest_run)),
        patch("agent.session_doctor.mark_recovery_resolved", new=AsyncMock()) as mark_resolved,
    ):
        report = await run_session_doctor(db, session=session)  # type: ignore[arg-type]

    assert report.repaired is True
    assert report.actions[0].action == "resolve_completed_auto_recovery"
    mark_resolved.assert_awaited_once()


@pytest.mark.asyncio
async def test_doctor_dry_run_reports_without_applying():
    session = _session("running")
    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=True)),
        patch("agent.session_doctor._active_or_queued_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor._latest_run", new=AsyncMock(return_value=None)),
        patch("agent.session_doctor.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        report = await run_session_doctor(_FakeDb(), session=session, dry_run=True)  # type: ignore[arg-type]

    assert report.repaired is False
    assert report.actions[0].applied is False
    update_status.assert_not_awaited()
    assert session.status == "running"


@pytest.mark.asyncio
async def test_doctor_lock_busy_does_not_repair():
    session = _session("running")
    with (
        patch("agent.session_doctor._try_session_lock", new=AsyncMock(return_value=(False, "session_lock_busy"))),
        patch("agent.session_doctor._active_or_queued_run", new=AsyncMock()) as active_run,
    ):
        report = await run_session_doctor(_FakeDb(), session=session)  # type: ignore[arg-type]

    assert report.lock_acquired is False
    assert report.repaired is False
    assert report.actions[0].action == "lock"
    assert report.actions[0].reason == "session_lock_busy"
    active_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_doctor_postgres_lock_error_does_not_repair():
    session = _session("running")

    report = await run_session_doctor(_PostgresLockErrorDb(), session=session)  # type: ignore[arg-type]

    assert report.lock_acquired is False
    assert report.repaired is False
    assert report.actions[0].reason == "lock_error:RuntimeError"
    assert session.status == "running"
