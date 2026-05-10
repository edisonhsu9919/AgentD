"""v0.4.9 Phase D scenario 5: worker crash leaves DB dirty projection;
session doctor can diagnose/repair, but prompt ingress stays unblocked.

Concretely: a worker dies between writing an assistant ``tool_call`` row
into the messages table and writing the matching ``tool_result``. After
restart, the DB tail is "dirty" but the LangGraph checkpoint may either be
clean (Phase A turned this into a no-op for runtime decisions) or itself
need repair via the doctor.

Properties pinned:

- prompt ingress accepts a new user prompt despite dirty DB tail (default
  flag off — Phase A behaviour holds).
- session_doctor's projection-repair action surfaces the dirty rows and
  marks them ``projection_state=discarded`` (or matching reason) when the
  checkpoint is the authoritative source.
- legacy strict path (rollback flag on) still rejects ingress, proving the
  toggle is the only thing that flips behaviour.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.runtime_integrity import (
    RuntimeGateAction,
    ValidationMessageSlice,
)


def _db_message(seq: int, role: str, parts: list[dict]):
    return SimpleNamespace(seq=seq, role=role, parts=parts)


def _open_tool_call_part(tool_call_id: str = "call_crashed"):
    return {
        "type": "tool_call",
        "tool_call_id": tool_call_id,
        "tool_name": "bash",
        "input": {"command": "echo crash"},
    }


@pytest.mark.asyncio
async def test_prompt_ingress_accepts_new_prompt_after_worker_crash_dirty_projection():
    """v0.4.9 default: a half-written tool_call left by a crashed worker
    must NOT permanently block the user from sending a new prompt.
    """
    from session.router import _enforce_prompt_runtime_integrity_gate

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    user = SimpleNamespace(workspace="/tmp/u")

    crashed_tail = ValidationMessageSlice(
        messages=[
            _db_message(1, "user", [{"type": "text", "content": "do bash"}]),
            _db_message(2, "assistant", [_open_tool_call_part()]),
        ],
        scope="recent_fallback",
        end_seq=2,
    )

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=crashed_tail),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
    ):
        # Must not raise — checkpoint clean (None) + dirty DB tail = ingress OK.
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, user)


@pytest.mark.asyncio
async def test_prompt_ingress_legacy_blocks_new_prompt_after_worker_crash(monkeypatch):
    """Legacy v0.4.4 strict path keeps the old "session has open runtime
    state" reject behind the rollback flag.
    """
    from core.config import settings as _settings
    from session.router import _enforce_prompt_runtime_integrity_gate

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    user = SimpleNamespace(workspace="/tmp/u")

    crashed_tail = ValidationMessageSlice(
        messages=[
            _db_message(1, "user", [{"type": "text", "content": "do bash"}]),
            _db_message(2, "assistant", [_open_tool_call_part()]),
        ],
        scope="recent_fallback",
        end_seq=2,
    )

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=crashed_tail),
        ),
        patch(
            "agent.projection_consistency.load_checkpoint_messages",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "agent.projection_consistency.inspect_db_checkpoint_projection",
            new=AsyncMock(return_value=SimpleNamespace(
                is_db_projection_ahead=False,
                to_dict=lambda: {},
            )),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
        pytest.raises(HTTPException) as excinfo,
    ):
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, user)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "SESSION_HAS_OPEN_RUNTIME_STATE"
    # Phase C contract: response must still carry actionable next steps.
    assert "available_actions" in excinfo.value.detail


@pytest.mark.asyncio
async def test_doctor_repairs_dirty_projection_left_by_crash(monkeypatch):
    """Worker-crash residue is exactly what the doctor's projection_repair
    action is for. Doctor inspects, finds DB-only ahead-of-checkpoint rows,
    and discards them — without invoking the model.
    """
    from agent.projection_consistency import (
        ProjectionConsistencyReport,
        ProjectionRepairResult,
    )
    from agent.session_doctor import run_session_doctor
    from agent import session_doctor as sd

    # Bypass advisory lock + skip unrelated repair branches.
    monkeypatch.setattr(sd, "_try_session_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(sd, "_repair_stale_active_status", AsyncMock())
    monkeypatch.setattr(sd, "_repair_waiting_without_pending_permission", AsyncMock())
    monkeypatch.setattr(sd, "_repair_recoverable_error_session", AsyncMock())
    monkeypatch.setattr(sd, "_repair_auto_recovery_resolution", AsyncMock())
    monkeypatch.setattr(sd, "_repair_subtask_waiting", AsyncMock())

    crashed_inspect = ProjectionConsistencyReport(
        db_tail_seq=2,
        db_open_tool_call_ids=["call_crashed"],
        db_ahead_tool_call_ids=["call_crashed"],
        is_db_projection_ahead=True,
        recommended_action="discard_uncommitted_db_projection",
        repairable_message_ids=["m_crashed"],
        repairable_message_seqs=[2],
    )
    repair_result = ProjectionRepairResult(
        repaired=True,
        discarded_message_ids=["m_crashed"],
        discarded_message_seqs=[2],
        discarded_tool_call_ids=["call_crashed"],
        reason="db_projection_ahead_of_checkpoint",
    )

    session = SimpleNamespace(id=uuid.uuid4(), status="error")
    user = SimpleNamespace(workspace="/tmp/u")

    with (
        patch(
            "agent.projection_consistency.inspect_session_projection_consistency",
            new=AsyncMock(return_value=(crashed_inspect, [])),
        ),
        patch(
            "agent.projection_consistency.repair_db_projection_ahead",
            new=AsyncMock(return_value=repair_result),
        ) as repair_mock,
        patch(
            "agent.projection_consistency.mark_latest_failed_run_projection_recoverable",
            new=AsyncMock(),
        ),
        patch("agent.session_doctor._record_doctor_action", new=AsyncMock()),
    ):
        report = await run_session_doctor(
            AsyncMock(), session=session, current_user=user,
        )

    repair_mock.assert_awaited_once()
    actions = {a.action: a for a in report.actions}
    proj_action = actions.get("repair_db_projection_ahead")
    assert proj_action is not None
    assert proj_action.applied is True
    assert "call_crashed" in proj_action.details["projection_repair"]["discarded_tool_call_ids"]
