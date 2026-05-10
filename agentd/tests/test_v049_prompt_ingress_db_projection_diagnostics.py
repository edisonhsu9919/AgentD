"""v0.4.9 Phase A: prompt ingress accepts dirty DB projection as diagnostics."""

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
async def test_prompt_ingress_accepts_when_db_tail_dirty_no_checkpoint():
    """The default flag-off behavior: dirty DB tail + missing checkpoint → accept."""
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
                    "tool_call_id": "call_a",
                    "tool_name": "bash",
                    "input": {},
                }]),
            ], scope="recent_fallback", end_seq=1)),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
    ):
        # Must not raise.
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, current_user)


@pytest.mark.asyncio
async def test_prompt_ingress_skips_projection_repair_in_default_path():
    """v0.4.9 Phase A: the runtime-path projection repair is gated behind the flag.

    With the default flag off, prompt ingress should not invoke
    inspect_db_checkpoint_projection / repair_db_projection_ahead.
    """
    from session.router import _enforce_prompt_runtime_integrity_gate

    session = SimpleNamespace(id=uuid.uuid4(), status="idle")
    current_user = SimpleNamespace(workspace="/tmp/user")

    with (
        patch("session.router._normalize_prompt_ingress_session_state", new=AsyncMock(return_value="idle")),
        patch("session.router._load_checkpoint_state", new=AsyncMock(return_value=None)),
        patch(
            "agent.runtime_integrity.load_terminal_validation_messages",
            new=AsyncMock(return_value=ValidationMessageSlice(messages=[], scope="recent_fallback")),
        ),
        patch("permission.service.get_pending_by_session", new=AsyncMock(return_value=[])),
        patch(
            "agent.projection_consistency.inspect_db_checkpoint_projection",
            new=AsyncMock(),
        ) as inspect_mock,
        patch(
            "agent.projection_consistency.repair_db_projection_ahead",
            new=AsyncMock(),
        ) as repair_mock,
    ):
        await _enforce_prompt_runtime_integrity_gate(AsyncMock(), session, current_user)

    inspect_mock.assert_not_awaited()
    repair_mock.assert_not_awaited()
