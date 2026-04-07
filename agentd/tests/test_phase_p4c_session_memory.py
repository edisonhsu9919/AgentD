"""Phase P4-C — Rolling Session Memory tests.

Tests cover:
- Memory file structure (template, read/write, meta)
- Token estimation
- Should-update threshold logic
- Memory structure validation
- Chapter parsing
- Patch prompt construction
- Update flow with mocked VLM
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.session_memory import (
    CHARS_PER_TOKEN,
    FIRST_BUILD_TOKEN_THRESHOLD,
    INCREMENTAL_TOKEN_THRESHOLD,
    MEMORY_CHAPTERS,
    MEMORY_TEMPLATE,
    MIN_TOOL_CALLS_TRIGGER,
    RECOMPRESSION_CHAPTER_LIMIT,
    RECOMPRESSION_TOKEN_LIMIT,
    _build_patch_prompt,
    _default_meta,
    _parse_chapters,
    _validate_memory_structure,
    estimate_tokens,
    get_memory_path,
    get_meta_path,
    read_memory,
    read_meta,
    should_update_memory,
    update_session_memory,
    write_memory,
    write_meta,
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


def _make_messages(count: int, content_size: int = 500) -> list:
    msgs = [SystemMessage(content="sys", id="s")]
    for i in range(count):
        msgs.append(HumanMessage(content=f"User {i}", id=f"h{i}"))
        msgs.append(AIMessage(content="Thinking", id=f"a{i}",
                              tool_calls=[{"id": f"tc{i}", "name": "grep", "args": {}}]))
        msgs.append(ToolMessage(content="x" * content_size, name="grep",
                                tool_call_id=f"tc{i}", id=f"t{i}"))
    msgs.append(AIMessage(content="Done", id="final"))
    return msgs


# ── File structure ───────────────────────────────────────────────────────


class TestFileStructure:
    def test_memory_template_has_all_chapters(self):
        for chapter in MEMORY_CHAPTERS:
            assert f"# {chapter}" in MEMORY_TEMPLATE

    def test_read_memory_none_when_missing(self, session_dir):
        assert read_memory(session_dir) is None

    def test_write_and_read_memory(self, session_dir):
        write_memory(session_dir, "# Test\ncontent")
        assert read_memory(session_dir) == "# Test\ncontent"

    def test_default_meta(self):
        meta = _default_meta()
        assert meta["memory_valid"] is False
        assert meta["pre_hard_compact"] is True
        assert meta["post_hard_compact"] is False
        assert meta["snapshot_version"] == 0

    def test_read_meta_defaults_when_missing(self, session_dir):
        meta = read_meta(session_dir)
        assert meta["memory_valid"] is False

    def test_write_and_read_meta(self, session_dir):
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["snapshot_version"] = 5
        write_meta(session_dir, meta)

        loaded = read_meta(session_dir)
        assert loaded["memory_valid"] is True
        assert loaded["snapshot_version"] == 5

    def test_paths(self, session_dir):
        assert get_memory_path(session_dir).endswith("session_memory.md")
        assert get_meta_path(session_dir).endswith("session_memory_meta.json")


# ── Token estimation ─────────────────────────────────────────────────────


class TestTokenEstimation:
    def test_estimate_tokens(self):
        text = "a" * 3000
        tokens = estimate_tokens(text)
        assert tokens == 3000 // CHARS_PER_TOKEN

    def test_empty_string(self):
        assert estimate_tokens("") == 0


# ── Should-update logic ──────────────────────────────────────────────────


class TestShouldUpdate:
    def test_no_messages_no_update(self, session_dir):
        assert should_update_memory(session_dir, [], 0) is False

    def test_first_build_below_threshold(self, session_dir):
        # Small messages → below 10k token threshold
        msgs = _make_messages(count=3, content_size=100)
        assert should_update_memory(session_dir, msgs, len(msgs) - 1) is False

    def test_first_build_above_threshold(self, session_dir):
        # Large messages → above 10k token threshold
        msgs = _make_messages(count=15, content_size=2000)
        assert should_update_memory(session_dir, msgs, len(msgs) - 1) is True

    def test_incremental_above_threshold(self, session_dir):
        # Simulate existing memory
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["compacted_through_seq"] = 5
        write_meta(session_dir, meta)
        write_memory(session_dir, MEMORY_TEMPLATE)

        # New messages with enough tokens
        msgs = _make_messages(count=10, content_size=2000)
        assert should_update_memory(session_dir, msgs, len(msgs) - 1) is True

    def test_incremental_by_tool_calls(self, session_dir):
        meta = _default_meta()
        meta["memory_valid"] = True
        meta["compacted_through_seq"] = 0
        write_meta(session_dir, meta)
        write_memory(session_dir, MEMORY_TEMPLATE)

        # 3+ tool calls with small content
        msgs = _make_messages(count=3, content_size=100)
        assert should_update_memory(session_dir, msgs, len(msgs) - 1) is True


# ── Structure validation ─────────────────────────────────────────────────


class TestValidation:
    def test_valid_template(self):
        assert _validate_memory_structure(MEMORY_TEMPLATE) is True

    def test_invalid_missing_chapters(self):
        assert _validate_memory_structure("# Random\nstuff") is False

    def test_valid_with_content(self):
        content = (
            "# Current State\nWorking on task\n"
            "# Task Specification\nAnalyze files\n"
            "# Next Steps\nContinue analysis\n"
        )
        assert _validate_memory_structure(content) is True


# ── Chapter parsing ──────────────────────────────────────────────────────


class TestParseChapters:
    def test_parse_template(self):
        chapters = _parse_chapters(MEMORY_TEMPLATE)
        assert "Session Title" in chapters
        assert "Current State" in chapters
        assert "Worklog" in chapters
        assert len(chapters) == len(MEMORY_CHAPTERS)

    def test_parse_with_content(self):
        content = "# Current State\nDoing stuff\n# Next Steps\nFinish"
        chapters = _parse_chapters(content)
        assert chapters["Current State"] == "Doing stuff"
        assert chapters["Next Steps"] == "Finish"


# ── Patch prompt ─────────────────────────────────────────────────────────


class TestPatchPrompt:
    def test_prompt_contains_current_memory(self):
        msgs = [AIMessage(content="Did something", id="a1")]
        prompt = _build_patch_prompt("# Current State\nIdle", msgs)
        assert "# Current State" in prompt
        assert "Idle" in prompt

    def test_prompt_contains_new_content(self):
        msgs = [AIMessage(content="Analyzed 5 files", id="a1")]
        prompt = _build_patch_prompt(MEMORY_TEMPLATE, msgs)
        assert "Analyzed 5 files" in prompt

    def test_truncates_long_messages(self):
        msgs = [AIMessage(content="x" * 10000, id="a1")]
        prompt = _build_patch_prompt(MEMORY_TEMPLATE, msgs)
        assert "[truncated]" in prompt


# ── Update flow with mocked VLM ─────────────────────────────────────────


class TestUpdateFlow:
    @pytest.mark.asyncio
    async def test_first_build_success(self, session_dir):
        updated_memory = (
            "# Session Title\nTest Session\n"
            "# Current State\nAnalyzing files\n"
            "# Task Specification\nProcess PDFs\n"
            "# Files and Artifacts\nreport.pdf\n"
            "# Workflow Patterns\n(none)\n"
            "# Errors & Corrections\n(none)\n"
            "# Active Skill / Plan\npdf-rename\n"
            "# Subtasks\n(none)\n"
            "# Key Results\n(none)\n"
            "# Next Steps\nContinue processing\n"
            "# Worklog\n- Started analysis\n"
        )

        msgs = _make_messages(count=5, content_size=500)

        with patch("agent.session_memory._call_compaction_model",
                    return_value=updated_memory):
            result = await update_session_memory(session_dir, msgs, "test-session")

        assert result is True
        memory = read_memory(session_dir)
        assert "Analyzing files" in memory

        meta = read_meta(session_dir)
        assert meta["memory_valid"] is True
        assert meta["snapshot_version"] == 1

    @pytest.mark.asyncio
    async def test_vlm_failure_keeps_old(self, session_dir):
        write_memory(session_dir, MEMORY_TEMPLATE)

        msgs = _make_messages(count=5)

        with patch("agent.session_memory._call_compaction_model", return_value=None):
            result = await update_session_memory(session_dir, msgs, "test-session")

        assert result is False
        # Old template preserved
        assert read_memory(session_dir) == MEMORY_TEMPLATE

    @pytest.mark.asyncio
    async def test_invalid_structure_discarded(self, session_dir):
        msgs = _make_messages(count=5)

        with patch("agent.session_memory._call_compaction_model",
                    return_value="Just random text without chapters"):
            result = await update_session_memory(session_dir, msgs, "test-session")

        assert result is False

    @pytest.mark.asyncio
    async def test_recompression_triggers(self, session_dir):
        # Create a large memory that exceeds recompression limit
        big_memory = MEMORY_TEMPLATE.replace("(empty)", "x " * 5000)

        compressed = (
            "# Session Title\nTest\n"
            "# Current State\nDone\n"
            "# Task Specification\nTask\n"
            "# Next Steps\nNothing\n"
        )

        msgs = _make_messages(count=5)
        call_count = [0]

        async def mock_call(prompt, max_tokens=4096):
            call_count[0] += 1
            if call_count[0] == 1:
                return big_memory  # First call: patch returns big
            return compressed  # Second call: recompression

        with patch("agent.session_memory._call_compaction_model", side_effect=mock_call):
            result = await update_session_memory(session_dir, msgs, "test-session")

        assert result is True
        # Should have called twice: patch + recompression
        assert call_count[0] == 2


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_thresholds(self):
        assert FIRST_BUILD_TOKEN_THRESHOLD == 10_000
        assert INCREMENTAL_TOKEN_THRESHOLD == 5_000
        assert MIN_TOOL_CALLS_TRIGGER == 3
        assert RECOMPRESSION_TOKEN_LIMIT == 12_000
        assert RECOMPRESSION_CHAPTER_LIMIT == 2_000

    def test_chapter_count(self):
        assert len(MEMORY_CHAPTERS) == 11
