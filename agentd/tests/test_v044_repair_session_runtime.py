"""v0.4.4 session runtime repair script tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace


def _msg(seq, role, parts, *, is_summary=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        seq=seq,
        role=role,
        parts=parts,
        is_summary=is_summary,
    )


def test_repair_plans_context_summary_move_after_tool_group():
    from scripts.repair_session_runtime import plan_summary_moves

    messages = [
        _msg(129, "assistant", [
            {"type": "tool_call", "tool_call_id": "call_00"},
            {"type": "tool_call", "tool_call_id": "call_01"},
            {"type": "tool_call", "tool_call_id": "call_02"},
        ]),
        _msg(130, "tool", [{"type": "tool_result", "tool_call_id": "call_02"}]),
        _msg(131, "user", [{"type": "text", "content": "[Context Summary]\nold"}], is_summary=True),
        _msg(132, "tool", [{"type": "tool_result", "tool_call_id": "call_00"}]),
        _msg(133, "tool", [{"type": "tool_result", "tool_call_id": "call_01"}]),
        _msg(134, "assistant", [{"type": "text", "content": "done"}]),
    ]

    moves = plan_summary_moves(messages)

    assert len(moves) == 1
    assert moves[0].summary_seq == 131
    assert moves[0].target_seq == 133
    assert moves[0].assistant_seq == 129
    assert moves[0].required_tool_call_ids == ["call_00", "call_01", "call_02"]


def test_repair_session_runtime_reports_checkpoint_dirty_even_when_db_tail_clean():
    from scripts.repair_session_runtime import _public_checkpoint_report

    report = _public_checkpoint_report({
        "checkpoint_valid": False,
        "invalid_indices": [3],
        "provider_payload_preflight_ok": False,
        "provider_payload_issues": [{
            "code": "assistant_tool_call_missing_tool_result",
            "tool_call_id": "call_00",
        }],
        "_agent": object(),
        "_messages": [object()],
    })

    assert report == {
        "checkpoint_valid": False,
        "invalid_indices": [3],
        "provider_payload_preflight_ok": False,
        "provider_payload_issues": [{
            "code": "assistant_tool_call_missing_tool_result",
            "tool_call_id": "call_00",
        }],
    }
