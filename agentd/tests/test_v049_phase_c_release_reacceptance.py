"""v0.4.9 Phase C re-acceptance: HITL release + DB projection coherence.

The Phase C audit found three real failures the original tests missed:

- F1: ``/release`` injected synthetic closures into the LangGraph checkpoint
  but did NOT update the DB messages projection. ``projection_can_append``
  then rejected the next assistant final, dropping it silently.
- F2: ``/runtime`` echoed the stale ``runtime_integrity_gate.open_tool_call_ids``
  even when the gate explicitly tagged them as
  ``checkpoint_clean_db_tail_diagnostics_only``.
- F3: ``release_session_to_idle`` returned ``released=True`` even when DB
  projection was still dirty.

These tests pin the corrected contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.message_persistence import projection_can_append


# ---------------------------------------------------------------------------
# Finding 3 (projection_can_append fail-soft default)
# ---------------------------------------------------------------------------


class _StubMessage:
    def __init__(self, role: str, parts: list[dict], seq: int = 1):
        self.role = role
        self.parts = parts
        self.seq = seq


@pytest.mark.asyncio
async def test_projection_can_append_allows_assistant_final_when_db_dirty_under_default_flag(monkeypatch):
    """v0.4.9 default: dirty DB tail must NOT silently block an assistant final."""
    from agent import message_persistence

    dirty_tail = [
        _StubMessage("user", [{"type": "text", "content": "go"}], seq=1),
        _StubMessage("assistant", [{
            "type": "tool_call",
            "tool_call_id": "call_x",
            "tool_name": "bash",
            "input": {},
        }], seq=2),
    ]

    monkeypatch.setattr(
        message_persistence.session_svc,
        "list_messages",
        AsyncMock(return_value=dirty_tail),
    )
    # Ensure default flag is off (Phase C re-acceptance contract).
    monkeypatch.setattr(
        message_persistence.settings,
        "runtime_integrity_gate_db_tail_enabled",
        False,
    )

    can_append = await projection_can_append(
        object(),  # not a Mock; real DB-shaped placeholder
        uuid.uuid4(),
        role="assistant",
        parts=[{"type": "text", "content": "final answer"}],
    )

    assert can_append is True


@pytest.mark.asyncio
async def test_projection_can_append_keeps_legacy_gate_under_rollback_flag(monkeypatch):
    """Rollback flag preserves v0.4.4 strict behaviour."""
    from agent import message_persistence

    dirty_tail = [
        _StubMessage("user", [{"type": "text", "content": "go"}], seq=1),
        _StubMessage("assistant", [{
            "type": "tool_call",
            "tool_call_id": "call_x",
            "tool_name": "bash",
            "input": {},
        }], seq=2),
    ]

    monkeypatch.setattr(
        message_persistence.session_svc,
        "list_messages",
        AsyncMock(return_value=dirty_tail),
    )
    monkeypatch.setattr(
        message_persistence.settings,
        "runtime_integrity_gate_db_tail_enabled",
        True,
    )

    can_append = await projection_can_append(
        object(),
        uuid.uuid4(),
        role="assistant",
        parts=[{"type": "text", "content": "final"}],
    )

    assert can_append is False


# ---------------------------------------------------------------------------
# Finding 1 (release inserts DB synthetic tool_result)
# ---------------------------------------------------------------------------


class _MutableMessage:
    """Minimal stand-in for the Message ORM row used by ``list_messages``."""

    def __init__(self, role, parts, seq):
        self.role = role
        self.parts = parts
        self.seq = seq


@pytest.mark.asyncio
async def test_release_inserts_db_synthetic_tool_result_for_dangling_tool_call():
    """After release, DB tail must contain a synthetic tool_result so projection clears."""
    from agent import session_release

    session = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="waiting",
        agent_id="assistant",
        model_id="m",
        parent_id=None,
    )
    user = SimpleNamespace(workspace="/tmp/u")

    db_messages = [
        _MutableMessage("user", [{"type": "text", "content": "run bash"}], seq=1),
        _MutableMessage("assistant", [{
            "type": "tool_call",
            "tool_call_id": "call_dangling",
            "tool_name": "bash",
            "input": {"command": "echo hi"},
        }], seq=2),
    ]
    create_calls: list[dict] = []

    async def fake_create_message(db, session_id, role, parts, **kwargs):
        create_calls.append({"role": role, "parts": list(parts)})

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0, scalar_one_or_none=lambda: None))

    fake_agent = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(values={"messages": []})),
        aupdate_state=AsyncMock(),
    )

    with (
        patch("session.service.update_session_status", new=AsyncMock()),
        patch("permission.service.cancel_pending_by_session", new=AsyncMock(return_value=1)),
        patch("session.service.list_messages", new=AsyncMock(return_value=db_messages)),
        patch("session.service.create_message", new=fake_create_message),
        patch("agent.runtime.build_agent", new=AsyncMock(return_value=fake_agent)),
        patch("workspace.manager.get_session_dir", return_value="/tmp/u/sessions/x"),
    ):
        result = await session_release.release_session_to_idle(
            db, session=session, current_user=user,
        )

    assert result.released is True
    assert "call_dangling" in result.db_synthetic_tool_result_ids
    # The DB synthetic message must be a tool role with a tool_result part
    # marked as synthetic_close + USER_RELEASED_TOOL_CALL.
    tool_creates = [c for c in create_calls if c["role"] == "tool"]
    assert tool_creates, "release should have created at least one synthetic tool_result row"
    parts = tool_creates[0]["parts"]
    assert parts[0]["type"] == "tool_result"
    assert parts[0]["tool_call_id"] == "call_dangling"
    assert parts[0]["is_error"] is True
    assert parts[0]["synthetic_close"] is True
    assert parts[0]["error_code"] == "USER_RELEASED_TOOL_CALL"
    assert parts[0]["release"] is True


@pytest.mark.asyncio
async def test_release_skips_db_synthetic_when_db_already_clean():
    """No-op: if all assistant tool_calls already have matching tool_results,
    release should not insert spurious synthetic rows."""
    from agent import session_release

    session = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="error",
        agent_id="assistant",
        model_id="m",
        parent_id=None,
    )
    user = SimpleNamespace(workspace="/tmp/u")

    db_messages = [
        _MutableMessage("user", [{"type": "text", "content": "run"}], seq=1),
        _MutableMessage("assistant", [{
            "type": "tool_call",
            "tool_call_id": "call_done",
            "tool_name": "bash",
            "input": {},
        }], seq=2),
        _MutableMessage("tool", [{
            "type": "tool_result",
            "tool_call_id": "call_done",
            "tool_name": "bash",
            "output": "ok",
        }], seq=3),
    ]
    create_calls: list[dict] = []

    async def fake_create_message(db, session_id, role, parts, **kwargs):
        create_calls.append({"role": role, "parts": list(parts)})

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0, scalar_one_or_none=lambda: None))

    fake_agent = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(values={"messages": []})),
        aupdate_state=AsyncMock(),
    )

    with (
        patch("session.service.update_session_status", new=AsyncMock()),
        patch("permission.service.cancel_pending_by_session", new=AsyncMock(return_value=0)),
        patch("session.service.list_messages", new=AsyncMock(return_value=db_messages)),
        patch("session.service.create_message", new=fake_create_message),
        patch("agent.runtime.build_agent", new=AsyncMock(return_value=fake_agent)),
        patch("workspace.manager.get_session_dir", return_value="/tmp/u/sessions/x"),
    ):
        result = await session_release.release_session_to_idle(
            db, session=session, current_user=user,
        )

    assert result.released is True
    assert result.db_synthetic_tool_result_ids == []
    assert [c for c in create_calls if c["role"] == "tool"] == []


# ---------------------------------------------------------------------------
# Finding 2 (/runtime ignores diagnostics-only DB-tail open ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_endpoint_ignores_diagnostics_only_open_tool_call_ids():
    """/runtime must not surface DB-tail open ids when the gate marks them diagnostics-only."""
    from session.router import get_runtime

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, workspace="/tmp/u")
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status="idle",
        updated_at=datetime.now(timezone.utc),
        title="t",
        agent_id="assistant",
        model_id="m",
        parent_id=None,
        token_usage={"input": 0, "output": 0, "total": 0},
        loaded_skills=[],
        is_internal=False,
    )

    diag_run = SimpleNamespace(
        id=uuid.uuid4(),
        diagnostics={
            "last_call_prompt_tokens": 10,
            "last_call_completion_tokens": 5,
            "context_window_limit": 8000,
            "context_usage_ratio": 0.001,
            "runtime_integrity_gate": {
                "checkpoint_state_kind": "provider_ready",
                "reason": "checkpoint_clean_db_tail_diagnostics_only",
                "open_tool_call_ids": ["stale_call_a"],
                "can_accept_user_prompt": True,
                "requires_human_input": False,
            },
        },
        run_type="start",
        status="completed",
        error=None,
        updated_at=datetime.now(timezone.utc),
    )

    # db.execute is called multiple times in get_runtime; we feed back-to-back
    # results: latest run, latest diag run, last error run, ... etc.
    class _Scalar:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    results = [
        _Scalar(diag_run),  # latest_run
        _Scalar(diag_run),  # last_run with diagnostics
        _Scalar(None),      # last_error_run
    ]

    db = AsyncMock()

    async def fake_execute(*_args, **_kwargs):
        if results:
            return results.pop(0)
        return _Scalar(None)

    db.execute = fake_execute

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
        patch("session.router.session_svc.get_last_message_seq", new=AsyncMock(return_value=2)),
        patch("permission.service.count_pending_by_session", new=AsyncMock(return_value=0)),
    ):
        response = await get_runtime(session_id=session_id, db=db, current_user=user)

    payload = response["data"] if isinstance(response, dict) and "data" in response else response
    assert payload["runtime_state"] == "provider_ready"
    assert payload["can_accept_user_prompt"] is True
    assert payload["requires_human_input"] is False
    # Critical: stale open ids must be hidden from active runtime state.
    assert payload["open_tool_call_ids"] == []


@pytest.mark.asyncio
async def test_runtime_endpoint_after_release_does_not_bleed_pre_release_hitl_state():
    """v0.4.9 Phase C followup: directly after /release, /runtime must surface
    the new clean state even though the latest run's diagnostics still record
    the pre-release HITL gate.
    """
    from session.router import get_runtime

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, workspace="/tmp/u")
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status="idle",  # Released back to idle.
        updated_at=datetime.now(timezone.utc),
        title="t",
        agent_id="assistant",
        model_id="m",
        parent_id=None,
        token_usage={"input": 0, "output": 0, "total": 0},
        loaded_skills=[],
        is_internal=False,
    )

    # The latest run is still the pre-release waiting run carrying HITL gate
    # diagnostics. Crucially, session_release_log was appended by the release
    # path so the runtime endpoint can recognize the situation.
    diag_run = SimpleNamespace(
        id=uuid.uuid4(),
        diagnostics={
            "last_call_prompt_tokens": 0,
            "last_call_completion_tokens": 0,
            "runtime_integrity_gate": {
                "checkpoint_state_kind": "hitl_open_tool_call",
                "reason": "hitl_open_tool_call_waiting",
                "open_tool_call_ids": ["call_was_pending"],
                "can_accept_user_prompt": False,
                "requires_human_input": True,
            },
            "session_release_log": [{
                "recorded_at": "2026-05-09T00:00:00+00:00",
                "cancelled_permission_count": 1,
                "closed_tool_call_ids": ["call_was_pending"],
            }],
        },
        run_type="start",
        status="failed",
        error=None,
        updated_at=datetime.now(timezone.utc),
    )

    class _Scalar:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    results = [
        _Scalar(diag_run),  # latest_run
        _Scalar(diag_run),  # last_run with diagnostics
        _Scalar(None),      # last_error_run
    ]

    db = AsyncMock()

    async def fake_execute(*_args, **_kwargs):
        if results:
            return results.pop(0)
        return _Scalar(None)

    db.execute = fake_execute

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
        patch("session.router.session_svc.get_last_message_seq", new=AsyncMock(return_value=2)),
        patch("permission.service.count_pending_by_session", new=AsyncMock(return_value=0)),
    ):
        response = await get_runtime(session_id=session_id, db=db, current_user=user)

    payload = response["data"] if isinstance(response, dict) and "data" in response else response
    # The session is idle and was released — runtime must reflect that.
    assert payload["can_accept_user_prompt"] is True
    assert payload["requires_human_input"] is False
    assert payload["open_tool_call_ids"] == []
    # Released sessions surface as provider_ready until the next run rewrites it.
    assert payload["runtime_state"] == "provider_ready"
