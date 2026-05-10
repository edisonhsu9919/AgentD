"""v0.4.9 Phase B: session_doctor exposes DB projection repair as an explicit action."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _ok_lock(monkeypatch):
    """Bypass advisory lock for unit tests."""
    from agent import session_doctor

    monkeypatch.setattr(
        session_doctor,
        "_try_session_lock",
        AsyncMock(return_value=(True, None)),
    )


def _no_other_repairs(monkeypatch):
    """Skip the other doctor repairs so test focuses on projection repair."""
    from agent import session_doctor

    monkeypatch.setattr(
        session_doctor, "_repair_stale_active_status", AsyncMock(),
    )
    monkeypatch.setattr(
        session_doctor, "_repair_waiting_without_pending_permission", AsyncMock(),
    )
    monkeypatch.setattr(
        session_doctor, "_repair_recoverable_error_session", AsyncMock(),
    )
    monkeypatch.setattr(
        session_doctor, "_repair_auto_recovery_resolution", AsyncMock(),
    )
    monkeypatch.setattr(
        session_doctor, "_repair_subtask_waiting", AsyncMock(),
    )


@pytest.mark.asyncio
async def test_doctor_skips_projection_repair_when_no_user(monkeypatch):
    """Without current_user, projection inspection is skipped silently."""
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    with patch(
        "agent.projection_consistency.inspect_session_projection_consistency",
        new=AsyncMock(),
    ) as inspect_mock:
        report = await run_session_doctor(
            AsyncMock(), session=session, current_user=None,
        )

    inspect_mock.assert_not_awaited()
    actions = [a.action for a in report.actions]
    assert "repair_db_projection_ahead" not in actions


@pytest.mark.asyncio
async def test_doctor_skips_projection_repair_when_session_active(monkeypatch):
    """Active runs may legitimately hold half-projected groups; doctor must not touch them."""
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    session = SimpleNamespace(id=uuid.uuid4(), status="running")
    user = SimpleNamespace(workspace="/tmp/u")

    with patch(
        "agent.projection_consistency.inspect_session_projection_consistency",
        new=AsyncMock(),
    ) as inspect_mock:
        report = await run_session_doctor(
            AsyncMock(), session=session, current_user=user,
        )

    inspect_mock.assert_not_awaited()
    actions = {a.action: a for a in report.actions}
    skip = actions.get("skip_projection_repair_active_run")
    assert skip is not None
    assert skip.applied is False


@pytest.mark.asyncio
async def test_doctor_no_action_when_projection_clean(monkeypatch):
    """Clean projection produces no doctor action entry (terse report)."""
    from agent.projection_consistency import ProjectionConsistencyReport
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    clean_report = ProjectionConsistencyReport(
        is_db_projection_ahead=False,
        is_checkpoint_projection_ahead=False,
    )

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    user = SimpleNamespace(workspace="/tmp/u")

    with patch(
        "agent.projection_consistency.inspect_session_projection_consistency",
        new=AsyncMock(return_value=(clean_report, [])),
    ):
        report = await run_session_doctor(
            AsyncMock(), session=session, current_user=user,
        )

    actions = [a.action for a in report.actions]
    assert "repair_db_projection_ahead" not in actions


@pytest.mark.asyncio
async def test_doctor_dry_run_inspects_but_does_not_repair(monkeypatch):
    """dry_run=True records the would-be action but skips repair_db_projection_ahead()."""
    from agent.projection_consistency import ProjectionConsistencyReport
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    dirty_report = ProjectionConsistencyReport(
        db_tail_seq=42,
        db_open_tool_call_ids=["call_dirty"],
        db_ahead_tool_call_ids=["call_dirty"],
        is_db_projection_ahead=True,
        recommended_action="discard_uncommitted_db_projection",
        repairable_message_ids=["m1"],
        repairable_message_seqs=[42],
    )

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    user = SimpleNamespace(workspace="/tmp/u")

    with (
        patch(
            "agent.projection_consistency.inspect_session_projection_consistency",
            new=AsyncMock(return_value=(dirty_report, [])),
        ),
        patch(
            "agent.projection_consistency.repair_db_projection_ahead",
            new=AsyncMock(),
        ) as repair_mock,
    ):
        report = await run_session_doctor(
            AsyncMock(), session=session, dry_run=True, current_user=user,
        )

    repair_mock.assert_not_awaited()
    actions = {a.action: a for a in report.actions}
    proj_action = actions.get("repair_db_projection_ahead")
    assert proj_action is not None
    assert proj_action.applied is False
    assert proj_action.reason == "dry_run"
    assert "projection_consistency" in proj_action.details


@pytest.mark.asyncio
async def test_doctor_applies_projection_repair_when_dirty(monkeypatch):
    """Real repair: doctor calls repair_db_projection_ahead and marks recoverable."""
    from agent.projection_consistency import (
        ProjectionConsistencyReport,
        ProjectionRepairResult,
    )
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    dirty_report = ProjectionConsistencyReport(
        db_tail_seq=42,
        db_open_tool_call_ids=["call_dirty"],
        db_ahead_tool_call_ids=["call_dirty"],
        is_db_projection_ahead=True,
        recommended_action="discard_uncommitted_db_projection",
        repairable_message_ids=["m1"],
        repairable_message_seqs=[42],
    )
    repair_result = ProjectionRepairResult(
        repaired=True,
        discarded_message_ids=["m1"],
        discarded_message_seqs=[42],
        discarded_tool_call_ids=["call_dirty"],
        reason="db_projection_ahead_of_checkpoint",
    )

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    user = SimpleNamespace(workspace="/tmp/u")

    with (
        patch(
            "agent.projection_consistency.inspect_session_projection_consistency",
            new=AsyncMock(return_value=(dirty_report, [])),
        ),
        patch(
            "agent.projection_consistency.repair_db_projection_ahead",
            new=AsyncMock(return_value=repair_result),
        ) as repair_mock,
        patch(
            "agent.projection_consistency.mark_latest_failed_run_projection_recoverable",
            new=AsyncMock(),
        ) as mark_mock,
        patch("agent.session_doctor._record_doctor_action", new=AsyncMock()),
    ):
        report = await run_session_doctor(
            AsyncMock(), session=session, dry_run=False, current_user=user,
        )

    repair_mock.assert_awaited_once()
    mark_mock.assert_awaited_once()
    actions = {a.action: a for a in report.actions}
    proj_action = actions.get("repair_db_projection_ahead")
    assert proj_action is not None
    assert proj_action.applied is True
    assert proj_action.reason == "db_projection_ahead_of_checkpoint"


@pytest.mark.asyncio
async def test_doctor_inspect_failure_yields_diagnostics_only(monkeypatch):
    """Inspection raising should not break the doctor; report records the skip."""
    from agent.session_doctor import run_session_doctor

    _ok_lock(monkeypatch)
    _no_other_repairs(monkeypatch)

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    user = SimpleNamespace(workspace="/tmp/u")

    with patch(
        "agent.projection_consistency.inspect_session_projection_consistency",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        report = await run_session_doctor(
            AsyncMock(), session=session, current_user=user,
        )

    actions = {a.action: a for a in report.actions}
    inspect_action = actions.get("inspect_db_projection_ahead")
    assert inspect_action is not None
    assert inspect_action.applied is False
    assert inspect_action.reason.startswith("inspect_failed:")
