"""v0.4.9 Phase C: No-Dead-Session contract — every 409 ingress rejection
must hand the user actionable next steps, and ``/release`` must reset open
runtime state to idle.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeGateDecision,
    ValidationMessageSlice,
)
from session.router import _resolve_open_runtime_actions


# ---------------------------------------------------------------------------
# 409 response augmentation
# ---------------------------------------------------------------------------


def test_resolve_actions_for_hitl_open_tool_call():
    decision = RuntimeGateDecision(
        action=RuntimeGateAction.REJECT_NEW_PROMPT,
        reason="hitl_open_tool_call_waiting",
        checkpoint_state_kind="hitl_open_tool_call",
        requires_human_input=True,
    )
    actions, message = _resolve_open_runtime_actions(decision)

    action_kinds = {a["action"] for a in actions}
    assert "approve_or_deny_pending_permission" in action_kinds
    assert "release" in action_kinds
    assert all("/api/sessions" in a["endpoint"] for a in actions)
    assert "approval" in message.lower() or "approve" in message.lower()


def test_resolve_actions_for_next_model_after_tool_result():
    decision = RuntimeGateDecision(
        action=RuntimeGateAction.REJECT_NEW_PROMPT,
        reason="checkpoint_next_model_after_tool_result",
        checkpoint_state_kind="next_model_after_tool_result",
    )
    actions, message = _resolve_open_runtime_actions(decision)

    action_kinds = {a["action"] for a in actions}
    assert action_kinds == {"retry", "release"}


def test_resolve_actions_for_subtask_waiting():
    decision = RuntimeGateDecision(
        action=RuntimeGateAction.REJECT_NEW_PROMPT,
        reason="session_subtask_waiting",
        checkpoint_state_kind="subtask_waiting",
    )
    actions, message = _resolve_open_runtime_actions(decision)

    action_kinds = {a["action"] for a in actions}
    assert "wait_for_subtask" in action_kinds
    assert "release" in action_kinds


def test_resolve_actions_default_is_doctor_or_release():
    decision = RuntimeGateDecision(
        action=RuntimeGateAction.REJECT_NEW_PROMPT,
        reason="checkpoint_invalid:invalid_unknown",
        checkpoint_state_kind="invalid_unknown",
    )
    actions, message = _resolve_open_runtime_actions(decision)

    action_kinds = {a["action"] for a in actions}
    assert "doctor" in action_kinds
    assert "release" in action_kinds


@pytest.mark.asyncio
async def test_prompt_ingress_409_includes_available_actions():
    """The full ingress 409 path returns ``available_actions`` in the response detail."""
    from session.router import _enforce_prompt_runtime_integrity_gate

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    user = SimpleNamespace(workspace="/tmp/u")

    # Decision: HITL open tool_call (so the gate rejects with actionable info).
    rejecting_decision = RuntimeGateDecision(
        action=RuntimeGateAction.REJECT_NEW_PROMPT,
        reason="hitl_open_tool_call_waiting",
        checkpoint_state_kind="hitl_open_tool_call",
        requires_human_input=True,
        open_tool_call_ids=["call_x"],
    )

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=ValidationMessageSlice(messages=[], scope="recent_fallback")),
        ),
        patch(
            "agent.runtime_integrity.RuntimeIntegrityGate.decide_prompt_ingress",
            return_value=rejecting_decision,
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, user)

    detail = excinfo.value.detail
    assert excinfo.value.status_code == 409
    assert detail["code"] == "SESSION_HAS_OPEN_RUNTIME_STATE"
    assert "available_actions" in detail
    actions = detail["available_actions"]
    assert isinstance(actions, list) and len(actions) >= 2
    assert {"action", "endpoint", "method", "description"} <= set(actions[0].keys())


# ---------------------------------------------------------------------------
# /release endpoint
# ---------------------------------------------------------------------------


def _open_tool_call_messages():
    return [
        HumanMessage(content="run"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_open_a", "name": "bash", "args": {}}],
            id="ai-1",
        ),
    ]


def _build_fake_agent(messages, post_messages=None):
    """Fake LangGraph-like agent: aget_state + aupdate_state."""
    state_holder = {"messages": list(messages)}

    class FakeAgent:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": list(state_holder["messages"])})

        async def aupdate_state(self, *, config, values, **kwargs):
            additions = values.get("messages", [])
            state_holder["messages"].extend(additions)
            if post_messages is not None:
                post_messages.extend(additions)

    return FakeAgent()


@pytest.mark.asyncio
async def test_release_cancels_pending_permissions_and_marks_idle():
    """Release cancels HITL approvals, marks session idle, and records audit."""
    from agent.session_release import release_session_to_idle

    session = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), status="error",
        agent_id="assistant", model_id="m", parent_id=None,
    )
    user = SimpleNamespace(workspace="/tmp/u")

    update_status = AsyncMock()
    cancel_perm = AsyncMock(return_value=2)
    fake_agent = _build_fake_agent(_open_tool_call_messages())

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1, scalar_one_or_none=lambda: None))

    with (
        patch("session.service.update_session_status", new=update_status),
        patch("permission.service.cancel_pending_by_session", new=cancel_perm),
        patch("agent.runtime.build_agent", new=AsyncMock(return_value=fake_agent)),
        patch("workspace.manager.get_session_dir", return_value="/tmp/u/sessions/x"),
    ):
        result = await release_session_to_idle(db, session=session, current_user=user)

    assert result.released is True
    assert result.cancelled_permission_count == 2
    assert "call_open_a" in result.closed_tool_call_ids
    update_status.assert_awaited()
    statuses = [call.args[2] for call in update_status.await_args_list]
    assert "idle" in statuses
    assert session.status == "idle"


@pytest.mark.asyncio
async def test_release_appends_synthetic_closures_to_checkpoint():
    """Release injects synthetic ToolMessage + release marker AIMessage into the checkpoint."""
    from agent.session_release import (
        RELEASE_AI_MESSAGE_CONTENT,
        SYNTHETIC_RELEASE_TOOL_CONTENT,
        release_session_to_idle,
    )

    session = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), status="error",
        agent_id="assistant", model_id="m", parent_id=None,
    )
    user = SimpleNamespace(workspace="/tmp/u")

    captured_appends: list = []
    fake_agent = _build_fake_agent(
        _open_tool_call_messages(), post_messages=captured_appends
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0, scalar_one_or_none=lambda: None))

    with (
        patch("session.service.update_session_status", new=AsyncMock()),
        patch("permission.service.cancel_pending_by_session", new=AsyncMock(return_value=0)),
        patch("agent.runtime.build_agent", new=AsyncMock(return_value=fake_agent)),
        patch("workspace.manager.get_session_dir", return_value="/tmp/u/sessions/x"),
    ):
        result = await release_session_to_idle(db, session=session, current_user=user)

    assert result.released is True
    # First append should be a ToolMessage closing call_open_a.
    tool_closures = [m for m in captured_appends if isinstance(m, ToolMessage)]
    assert any(
        getattr(m, "tool_call_id", None) == "call_open_a"
        and SYNTHETIC_RELEASE_TOOL_CONTENT in (m.content or "")
        for m in tool_closures
    )
    # And a release-marker AIMessage at the end.
    ai_markers = [m for m in captured_appends if isinstance(m, AIMessage)]
    assert ai_markers, "release should append at least one release-marker AIMessage"
    assert RELEASE_AI_MESSAGE_CONTENT in (ai_markers[-1].content or "")


@pytest.mark.asyncio
async def test_release_handles_empty_checkpoint_gracefully():
    """Release on a never-started session still returns released=True."""
    from agent.session_release import release_session_to_idle

    session = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), status="error",
        agent_id="assistant", model_id="m", parent_id=None,
    )
    user = SimpleNamespace(workspace="/tmp/u")

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0, scalar_one_or_none=lambda: None))

    with (
        patch("session.service.update_session_status", new=AsyncMock()),
        patch("permission.service.cancel_pending_by_session", new=AsyncMock(return_value=0)),
        # build_agent fails → release should still succeed and set idle.
        patch("agent.runtime.build_agent", new=AsyncMock(side_effect=RuntimeError("not_built"))),
        patch("workspace.manager.get_session_dir", return_value="/tmp/u/sessions/x"),
    ):
        result = await release_session_to_idle(db, session=session, current_user=user)

    assert result.released is True
    assert result.closed_tool_call_ids == []
    assert session.status == "idle"


@pytest.mark.asyncio
async def test_release_endpoint_rejects_busy_session():
    """The release endpoint must not run while session is running/queued."""
    from session.router import release_session_runtime

    session_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), workspace="/tmp/u")
    session = SimpleNamespace(
        id=session_id, user_id=user.id, status="running",
        agent_id="assistant", model_id="m", parent_id=None,
    )

    with (
        patch("session.router.session_svc.get_session", new=AsyncMock(return_value=session)),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await release_session_runtime(session_id=session_id, db=AsyncMock(), current_user=user)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "SESSION_BUSY"
    assert "available_actions" in excinfo.value.detail
