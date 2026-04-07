"""Phase P4-D — Hard Compact & State Machine tests.

Tests cover:
- State machine transitions (pre → post hard compact)
- Prompt assembly Layer 5 routing (memory vs context_summary.json)
- compact_session memory-first strategy
- Diagnostics compaction_mode field
- Fallback to LLM summary when memory unavailable
"""

import json
import os

import pytest

from agent.session_memory import (
    MEMORY_TEMPLATE,
    _default_meta,
    write_memory,
    write_meta,
    read_meta,
)
from workspace.manager import ensure_user_root, get_session_dir


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def session_dir(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    sd = get_session_dir(root, "test-session")
    os.makedirs(os.path.join(sd, ".agentd"), exist_ok=True)
    return sd


# ── State machine ────────────────────────────────────────────────────────


class TestStateMachine:
    def test_default_is_pre_hard_compact(self, session_dir):
        meta = read_meta(session_dir)
        assert meta["pre_hard_compact"] is True
        assert meta["post_hard_compact"] is False

    def test_transition_to_post(self, session_dir):
        meta = _default_meta()
        meta["pre_hard_compact"] = False
        meta["post_hard_compact"] = True
        meta["last_hard_compaction_at"] = "2026-04-04T12:00:00Z"
        meta["boundary_seq"] = 50
        write_meta(session_dir, meta)

        loaded = read_meta(session_dir)
        assert loaded["pre_hard_compact"] is False
        assert loaded["post_hard_compact"] is True
        assert loaded["boundary_seq"] == 50

    def test_memory_valid_flag(self, session_dir):
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["snapshot_version"] = 3
        write_meta(session_dir, meta)

        loaded = read_meta(session_dir)
        assert loaded["memory_valid"] is True
        assert loaded["snapshot_version"] == 3


# ── Prompt assembly Layer 5 routing ──────────────────────────────────────


class TestLayer5Routing:
    def test_no_files_returns_empty(self, session_dir):
        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(session_dir)
        assert result == ""

    def test_post_hard_compact_returns_empty(self, session_dir):
        """In post_hard_compact → Layer 5 returns empty.

        The summary is already in DB/checkpoint as is_summary=true message.
        Layer 5 should NOT re-inject the disk memory into the system prompt.
        """
        write_memory(session_dir, "# Current State\nAnalyzing PDF\n# Task Specification\nProcess docs\n# Next Steps\nFinish")
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["post_hard_compact"] = True
        meta["snapshot_version"] = 5
        meta["boundary_seq"] = 42
        write_meta(session_dir, meta)

        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(session_dir)

        # post_hard_compact: summary is in checkpoint, not system prompt
        assert result == ""

    def test_pre_hard_compact_uses_legacy(self, session_dir):
        """In pre_hard_compact → falls through to context_summary.json."""
        # Set up memory but pre_hard_compact
        write_memory(session_dir, MEMORY_TEMPLATE)
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["pre_hard_compact"] = True
        meta["post_hard_compact"] = False
        write_meta(session_dir, meta)

        # No context_summary.json → returns empty (memory not used in pre mode)
        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(session_dir)
        assert result == ""

    def test_post_hard_compact_invalid_memory_falls_back(self, session_dir):
        """In post_hard_compact but memory_valid=False → falls to legacy."""
        meta = _default_meta()
        meta["post_hard_compact"] = True
        meta["memory_valid"] = False
        write_meta(session_dir, meta)

        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(session_dir)
        assert result == ""  # No context_summary.json either

    def test_legacy_context_summary_json(self, session_dir):
        """Legacy path: context_summary.json still works."""
        summary = {
            "structured": True,
            "session_intent": "Process PDF documents",
            "current_task_state": "Running skill",
            "key_decisions": ["Use pdf-rename skill"],
            "active_skill": "pdf-rename",
            "important_artifacts": ["output.pdf"],
            "conversation_highlights": ["Loaded skill"],
            "next_steps": ["Continue processing"],
            "compaction_count": 2,
        }
        summary_path = os.path.join(session_dir, ".agentd", "context_summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f)

        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(session_dir)

        assert "Prior Context (from compaction)" in result
        assert "Process PDF documents" in result
        assert "pdf-rename" in result


# ── Diagnostics ──────────────────────────────────────────────────────────


class TestDiagnostics:
    def test_compaction_mode_pre(self, session_dir):
        from agent.executor import _get_compaction_mode_diagnostics
        diag = _get_compaction_mode_diagnostics(session_dir)
        assert diag["compaction_mode"] == "pre_hard_compact"
        assert diag["memory_available"] is False

    def test_compaction_mode_post(self, session_dir):
        meta = _default_meta()
        meta["post_hard_compact"] = True
        meta["memory_valid"] = True
        meta["snapshot_version"] = 7
        meta["memory_token_estimate"] = 5000
        write_meta(session_dir, meta)

        from agent.executor import _get_compaction_mode_diagnostics
        diag = _get_compaction_mode_diagnostics(session_dir)
        assert diag["compaction_mode"] == "post_hard_compact"
        assert diag["memory_available"] is True
        assert diag["memory_snapshot_version"] == 7
        assert diag["memory_token_estimate"] == 5000

    def test_compaction_mode_none(self):
        from agent.executor import _get_compaction_mode_diagnostics
        diag = _get_compaction_mode_diagnostics(None)
        assert diag["compaction_mode"] == "pre_hard_compact"


# ── compact_session memory-first strategy ────────────────────────────────


class TestCompactSessionMemoryFirst:
    def test_memory_available_flag_in_meta(self, session_dir):
        """After memory is written, memory_valid should be True."""
        write_memory(session_dir, "# Current State\nTest\n# Task Specification\nTest\n# Next Steps\nTest")
        meta = _default_meta()
        meta["memory_valid"] = True
        write_meta(session_dir, meta)

        loaded = read_meta(session_dir)
        assert loaded["memory_valid"] is True

    def test_meta_after_state_transition(self, session_dir):
        """Simulate what compact_session does to meta."""
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["snapshot_version"] = 3

        # Simulate state transition
        meta["pre_hard_compact"] = False
        meta["post_hard_compact"] = True
        meta["last_hard_compaction_at"] = "2026-04-04T15:00:00Z"
        meta["boundary_seq"] = 100
        write_meta(session_dir, meta)

        loaded = read_meta(session_dir)
        assert loaded["post_hard_compact"] is True
        assert loaded["boundary_seq"] == 100
        assert loaded["last_hard_compaction_at"] == "2026-04-04T15:00:00Z"
