"""Phase P4-A — max_result_size_chars & Result Budget Control tests.

Tests cover:
- ToolMetadata.max_result_size_chars field
- Per-tool override values
- _truncate_to_artifact logic
- Registry coroutine budget enforcement
"""

import os
from dataclasses import asdict

import pytest

from tools.base import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    MAX_RESULTS_PER_TURN_CHARS,
    RESULT_SIZE_UNLIMITED,
    ToolMetadata,
)
from tools.registry import get_registry, _truncate_to_artifact


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_default_max_result_size(self):
        assert DEFAULT_MAX_RESULT_SIZE_CHARS == 50_000

    def test_max_per_turn(self):
        assert MAX_RESULTS_PER_TURN_CHARS == 200_000

    def test_unlimited_sentinel(self):
        assert RESULT_SIZE_UNLIMITED == -1


# ── ToolMetadata field ───────────────────────────────────────────────────


class TestMetadataField:
    def test_default_value(self):
        meta = ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="session_only",
            mutates_session_state=False,
        )
        assert meta.max_result_size_chars == DEFAULT_MAX_RESULT_SIZE_CHARS

    def test_custom_value(self):
        meta = ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="session_only",
            mutates_session_state=False,
            max_result_size_chars=30_000,
        )
        assert meta.max_result_size_chars == 30_000

    def test_unlimited(self):
        meta = ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="medium",
            access_scope="session_only",
            mutates_session_state=False,
            max_result_size_chars=RESULT_SIZE_UNLIMITED,
        )
        assert meta.max_result_size_chars == -1

    def test_field_in_asdict(self):
        meta = ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=True,
            is_concurrency_safe=False,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="unrestricted",
            mutates_session_state=False,
        )
        d = asdict(meta)
        assert "max_result_size_chars" in d
        assert d["max_result_size_chars"] == DEFAULT_MAX_RESULT_SIZE_CHARS


# ── Per-tool overrides ───────────────────────────────────────────────────


class TestPerToolOverrides:
    def test_file_read_unlimited(self):
        meta = get_registry().get("file_read").metadata
        assert meta.max_result_size_chars == RESULT_SIZE_UNLIMITED

    def test_file_inspect_30k(self):
        meta = get_registry().get("file_inspect").metadata
        assert meta.max_result_size_chars == 30_000

    def test_planning_100k(self):
        meta = get_registry().get("planning").metadata
        assert meta.max_result_size_chars == 100_000

    def test_bash_default_50k(self):
        meta = get_registry().get("bash").metadata
        assert meta.max_result_size_chars == DEFAULT_MAX_RESULT_SIZE_CHARS

    def test_grep_default_50k(self):
        meta = get_registry().get("grep").metadata
        assert meta.max_result_size_chars == DEFAULT_MAX_RESULT_SIZE_CHARS

    def test_all_tools_have_field(self):
        registry = get_registry()
        for name, tool in registry.tools.items():
            val = tool.metadata.max_result_size_chars
            assert isinstance(val, int), f"{name} max_result_size_chars is not int"


# ── _truncate_to_artifact ────────────────────────────────────────────────


class TestTruncateToArtifact:
    def test_saves_full_output(self, tmp_path):
        session_dir = str(tmp_path)
        output = "x" * 100_000
        result = _truncate_to_artifact(output, 50_000, "bash", session_dir)

        # Full output saved as artifact
        artifact_dir = os.path.join(session_dir, ".agentd", "artifacts")
        assert os.path.isdir(artifact_dir)
        files = os.listdir(artifact_dir)
        assert len(files) == 1
        assert files[0].startswith("bash_")

        with open(os.path.join(artifact_dir, files[0])) as f:
            saved = f.read()
        assert len(saved) == 100_000

    def test_returns_preview_with_ref(self, tmp_path):
        session_dir = str(tmp_path)
        output = "line\n" * 20_000  # ~100k chars
        result = _truncate_to_artifact(output, 50_000, "grep", session_dir)

        assert "Result truncated" in result
        assert ".agentd/artifacts/grep_" in result
        assert "100,000 chars" in result
        # Preview is roughly 80% of budget
        preview_part = result.split("--- [Result truncated")[0]
        assert len(preview_part) < 50_000

    def test_creates_artifact_dir(self, tmp_path):
        session_dir = str(tmp_path)
        # .agentd/artifacts/ doesn't exist yet
        assert not os.path.exists(os.path.join(session_dir, ".agentd"))

        _truncate_to_artifact("big output", 5, "test", session_dir)
        assert os.path.isdir(os.path.join(session_dir, ".agentd", "artifacts"))
