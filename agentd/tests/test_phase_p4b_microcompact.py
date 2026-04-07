"""Phase P4-B — Microcompact tests.

Tests cover:
- Candidate identification (compressible tool results)
- Protection logic (frontier turns, protected tools, subtask results)
- Trigger threshold logic
- MicrocompactResult structure
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.microcompact import (
    FRONTIER_TURNS,
    HIGH_COMPRESS_TOOLS,
    MIN_COMPRESSIBLE_COUNT,
    PROTECTED_TOOL_NAMES,
    RATIO_THRESHOLD,
    SINGLE_MSG_SIZE_THRESHOLD,
    MicrocompactResult,
    _find_compressible_candidates,
    _find_protected_indices,
    run_microcompact,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _tool_msg(name: str, content: str, msg_id: str = "t1") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="tc1", id=msg_id)


def _build_messages(tool_count: int = 10, content_size: int = 500) -> list:
    """Build a realistic message list with system + user + AI + tools."""
    msgs = [SystemMessage(content="System prompt", id="sys")]
    for i in range(tool_count):
        msgs.append(HumanMessage(content=f"User message {i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"Thinking...", id=f"ai{i}",
                              tool_calls=[{"id": f"tc{i}", "name": "grep", "args": {}}]))
        msgs.append(_tool_msg("grep", "x" * content_size, f"tool{i}"))
    # Final assistant response
    msgs.append(AIMessage(content="Done!", id="ai_final"))
    return msgs


# ── Candidate identification ─────────────────────────────────────────────


class TestFindCandidates:
    def test_identifies_tool_messages(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="hello", id="h"),
            AIMessage(content="thinking", id="a"),
            _tool_msg("grep", "x" * 500, "t1"),
            _tool_msg("bash", "y" * 1000, "t2"),
        ]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 2
        assert candidates[0]["tool_name"] == "grep"
        assert candidates[1]["tool_name"] == "bash"

    def test_high_compress_tool_enters_even_when_small(self):
        """High-compressibility tools enter candidates regardless of size."""
        msgs = [_tool_msg("grep", "No matches found.", "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 1  # grep is high-compress → always enters
        assert candidates[0]["is_high_compress"] is True

    def test_non_high_compress_skips_small(self):
        """Non-high-compress tools still need chars >= 200."""
        msgs = [_tool_msg("file_write", "ok", "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 0  # file_write is not high-compress, < 200 chars

    def test_skips_non_tool_messages(self):
        msgs = [
            HumanMessage(content="x" * 500, id="h"),
            AIMessage(content="x" * 500, id="a"),
        ]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 0

    def test_reports_char_count(self):
        msgs = [_tool_msg("bash", "a" * 5000, "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert candidates[0]["chars"] == 5000


# ── Protection logic ─────────────────────────────────────────────────────


class TestFindProtected:
    def test_protects_system_message(self):
        msgs = [SystemMessage(content="sys", id="s"), HumanMessage(content="h", id="h")]
        protected = _find_protected_indices(msgs)
        assert 0 in protected

    def test_protects_frontier_turns(self):
        msgs = _build_messages(tool_count=5)
        protected = _find_protected_indices(msgs)
        # Last FRONTIER_TURNS=2 complete turns should be protected
        # That means the last few messages including the final AI response
        last_idx = len(msgs) - 1
        assert last_idx in protected  # final AI response
        assert last_idx - 1 in protected  # last tool result

    def test_protects_planning_tool(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h", id="h1"),
            AIMessage(content="plan", id="a1"),
            _tool_msg("planning", "task plan content here with enough chars" * 10, "plan1"),
            _tool_msg("grep", "old grep result with enough chars" * 10, "grep1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 3 in protected  # planning tool result

    def test_protects_subtask_result(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            AIMessage(content="[Sub-task completed]\nChild result here", id="a1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 1 in protected

    def test_protects_context_summary(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="[Context Summary]\nPrevious session...", id="h1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 1 in protected


# ── Trigger thresholds ───────────────────────────────────────────────────


class TestTriggerThresholds:
    @pytest.mark.asyncio
    async def test_too_few_messages(self):
        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": [SystemMessage(content="sys", id="s")]}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)

        result = await run_microcompact(mock_agent, {}, "test-session")
        assert result.applied is False
        assert result.reason == "too_few_messages"

    @pytest.mark.asyncio
    async def test_no_snapshot(self):
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=None)

        result = await run_microcompact(mock_agent, {}, "test-session")
        assert result.applied is False
        assert result.reason == "no_snapshot"

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """Few small candidates + low ratio → no action."""
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h1", id="h1"),
            AIMessage(content="a1", id="a1"),
            _tool_msg("grep", "x" * 300, "t1"),
            HumanMessage(content="h2", id="h2"),
            AIMessage(content="a2", id="a2"),
        ]
        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.3)
        assert result.applied is False

    @pytest.mark.asyncio
    async def test_triggers_on_high_ratio(self):
        """High context ratio with enough candidates → triggers."""
        msgs = _build_messages(tool_count=10, content_size=500)

        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)
        mock_agent.aupdate_state = AsyncMock()

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.75)
        assert result.applied is True
        assert "ratio" in result.reason

    @pytest.mark.asyncio
    async def test_triggers_on_oversized_message(self):
        """Single oversized message outside frontier → always triggers."""
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h1", id="h1"),
            AIMessage(content="a1", id="a1"),
            _tool_msg("bash", "x" * 50_000, "t1"),  # oversized, old turn
            HumanMessage(content="h2", id="h2"),
            AIMessage(content="a2", id="a2"),
            _tool_msg("grep", "x" * 300, "t2"),  # recent turn 1
            HumanMessage(content="h3", id="h3"),
            AIMessage(content="a3", id="a3"),
            _tool_msg("grep", "x" * 300, "t3"),  # recent turn 2
            HumanMessage(content="h4", id="h4"),
            AIMessage(content="done", id="a4"),
        ]

        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)
        mock_agent.aupdate_state = AsyncMock()

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.3)
        assert result.applied is True
        assert "oversized" in result.reason


# ── MicrocompactResult ───────────────────────────────────────────────────


class TestMicrocompactResult:
    def test_structure(self):
        r = MicrocompactResult(applied=True, removed_count=3, replaced_count=1, reason="ratio=0.75")
        assert r.applied is True
        assert r.removed_count == 3
        assert r.replaced_count == 1

    def test_not_applied(self):
        r = MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="below_threshold")
        assert r.applied is False


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_ratio_threshold(self):
        assert RATIO_THRESHOLD == 0.6

    def test_min_compressible(self):
        assert MIN_COMPRESSIBLE_COUNT == 8

    def test_frontier_turns(self):
        assert FRONTIER_TURNS == 2

    def test_protected_tools(self):
        assert "planning" in PROTECTED_TOOL_NAMES
        assert "todo_update" in PROTECTED_TOOL_NAMES
        assert "skill" in PROTECTED_TOOL_NAMES

    def test_high_compress_tools(self):
        assert "grep" in HIGH_COMPRESS_TOOLS
        assert "list_dir" in HIGH_COMPRESS_TOOLS
        assert "bash" in HIGH_COMPRESS_TOOLS
