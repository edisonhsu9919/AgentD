"""Prompt ingress runtime integrity guard tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.runtime_integrity import ValidationMessageSlice


def _db_message(seq: int, role: str, parts: list[dict]):
    return SimpleNamespace(seq=seq, role=role, parts=parts)


@pytest.mark.asyncio
async def test_prompt_ingress_accepts_dirty_db_tail_when_checkpoint_absent_under_default_flag():
    """v0.4.9 Phase A: with the rollback flag off (default), DB tail dirty alone
    should not produce SESSION_HAS_OPEN_RUNTIME_STATE.
    """
    from session.router import _enforce_prompt_runtime_integrity_gate

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    current_user = SimpleNamespace(workspace="/tmp/user")

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=ValidationMessageSlice(messages=[
                _db_message(1, "assistant", [{
                    "type": "tool_call",
                    "tool_call_id": "call_edit",
                    "tool_name": "file_edit",
                    "input": {},
                }]),
            ], scope="recent_fallback", end_seq=1)),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
    ):
        # Should NOT raise — checkpoint absent + DB dirty must accept ingress.
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, current_user)


@pytest.mark.asyncio
async def test_prompt_ingress_legacy_rejects_open_tool_call_under_flag(monkeypatch):
    """Legacy v0.4.4 behavior is preserved behind the rollback flag."""
    from core.config import settings as _settings
    from session.router import _enforce_prompt_runtime_integrity_gate

    monkeypatch.setattr(_settings, "runtime_integrity_gate_db_tail_enabled", True)

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    current_user = SimpleNamespace(workspace="/tmp/user")

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=ValidationMessageSlice(messages=[
                _db_message(1, "assistant", [{
                    "type": "tool_call",
                    "tool_call_id": "call_edit",
                    "tool_name": "file_edit",
                    "input": {},
                }]),
            ], scope="recent_fallback", end_seq=1)),
        ),
        patch("session.router._load_checkpoint_messages_for_repair", create=True, new=AsyncMock(return_value=[])),
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
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, current_user)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "SESSION_HAS_OPEN_RUNTIME_STATE"
    assert excinfo.value.detail["open_tool_call_ids"] == ["call_edit"]


@pytest.mark.asyncio
async def test_prompt_ingress_allows_clean_tail():
    from session.router import _enforce_prompt_runtime_integrity_gate

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    current_user = SimpleNamespace(workspace="/tmp/user")

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=ValidationMessageSlice(messages=[
                _db_message(1, "user", [{"type": "text", "content": "hi"}]),
                _db_message(2, "assistant", [{"type": "text", "content": "done"}]),
            ], scope="recent_fallback", end_seq=2)),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
    ):
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, current_user)


@pytest.mark.asyncio
async def test_prompt_ingress_normalizes_stale_subtask_waiting():
    from session.router import _normalize_prompt_ingress_session_state

    class _Result:
        def scalar_one_or_none(self):
            return None

    db = AsyncMock()
    db.execute.return_value = _Result()
    session = SimpleNamespace(id=uuid.uuid4(), status="subtask_waiting")

    with (
        patch(
            "agent.subtask_bridge.bridge_reconcilable_child_tasks",
            new=AsyncMock(return_value=SimpleNamespace(enqueued_run_id=None)),
        ),
        patch(
            "agent.subtask_reconciliation.reconcile_completed_child_tasks",
            new=AsyncMock(),
        ) as reconcile,
        patch("session.router.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        status = await _normalize_prompt_ingress_session_state(db, session)

    assert status == "idle"
    assert session.status == "idle"
    reconcile.assert_awaited_once()
    update_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_prompt_ingress_skips_reconciliation_when_idle_parent_has_no_active_child():
    from session.router import _normalize_prompt_ingress_session_state

    class _Result:
        def scalar_one_or_none(self):
            return None

    db = AsyncMock()
    db.execute.return_value = _Result()
    session = SimpleNamespace(id=uuid.uuid4(), status="idle")

    with (
        patch(
            "agent.subtask_bridge.bridge_reconcilable_child_tasks",
            new=AsyncMock(return_value=SimpleNamespace(enqueued_run_id=None)),
        ),
        patch(
            "agent.subtask_reconciliation.reconcile_completed_child_tasks",
            new=AsyncMock(),
        ) as reconcile,
        patch("session.router.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        status = await _normalize_prompt_ingress_session_state(db, session)

    assert status == "idle"
    reconcile.assert_not_awaited()
    update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_ingress_reconciles_idle_parent_with_stale_completed_child_task():
    from session.router import _normalize_prompt_ingress_session_state

    class _ActiveResult:
        def scalar_one_or_none(self):
            return uuid.uuid4()

    class _EmptyResult:
        def scalar_one_or_none(self):
            return None

    db = AsyncMock()
    db.execute.side_effect = [_ActiveResult(), _EmptyResult()]
    session = SimpleNamespace(id=uuid.uuid4(), status="idle")

    with (
        patch(
            "agent.subtask_bridge.bridge_reconcilable_child_tasks",
            new=AsyncMock(return_value=SimpleNamespace(enqueued_run_id=None)),
        ),
        patch(
            "agent.subtask_reconciliation.reconcile_completed_child_tasks",
            new=AsyncMock(),
        ) as reconcile,
        patch("session.router.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        status = await _normalize_prompt_ingress_session_state(db, session)

    assert status == "idle"
    assert session.status == "idle"
    reconcile.assert_awaited_once()
    update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_ingress_marks_idle_parent_subtask_waiting_when_child_still_active():
    from session.router import _normalize_prompt_ingress_session_state

    class _Result:
        def scalar_one_or_none(self):
            return uuid.uuid4()

    db = AsyncMock()
    db.execute.return_value = _Result()
    session = SimpleNamespace(id=uuid.uuid4(), status="idle")

    with (
        patch(
            "agent.subtask_bridge.bridge_reconcilable_child_tasks",
            new=AsyncMock(return_value=SimpleNamespace(enqueued_run_id=None)),
        ),
        patch(
            "agent.subtask_reconciliation.reconcile_completed_child_tasks",
            new=AsyncMock(),
        ) as reconcile,
        patch("session.router.session_svc.update_session_status", new=AsyncMock()) as update_status,
    ):
        status = await _normalize_prompt_ingress_session_state(db, session)

    assert status == "subtask_waiting"
    assert session.status == "subtask_waiting"
    reconcile.assert_awaited_once()
    update_status.assert_awaited_once()
