"""Tests for Phase K — <think> tag stripping, reasoning extraction, reasoning_delta SSE.

Covers:
  - _ThinkFilter streaming filter (stateful, handles split chunks, returns delta)
  - _strip_model_tags (complete text stripping)
  - _extract_reasoning (reasoning content extraction)
  - _persist_messages stripping (source inspection)
  - _stream_and_translate filtering + reasoning_delta (source inspection)
  - Frontmatter YAML quotes stripping
"""

import inspect

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# K: ThinkFilter — stateful streaming filter
# ═══════════════════════════════════════════════════════════════════════════════


class TestThinkFilter:
    """Verify _ThinkFilter correctly strips <think> blocks from token streams."""

    def _make_filter(self):
        from agent.executor import _ThinkFilter
        return _ThinkFilter()

    def test_no_think_tags(self):
        """Pass-through when no <think> tags present."""
        f = self._make_filter()
        text, rdelta = f.feed("Hello world")
        assert text == "Hello world"
        assert rdelta == ""
        assert f.flush() == ""

    def test_simple_think_block(self):
        """Single <think> block stripped in one chunk."""
        f = self._make_filter()
        text, rdelta = f.feed("<think>reasoning here</think>The answer is 42.")
        assert text == "The answer is 42."
        assert rdelta == "reasoning here"
        assert f.reasoning == "reasoning here"

    def test_think_block_only(self):
        """Message that is entirely a think block."""
        f = self._make_filter()
        text, rdelta = f.feed("<think>just reasoning</think>")
        assert text == ""
        assert rdelta == "just reasoning"

    def test_think_split_across_chunks(self):
        """<think> tag split across multiple chunks."""
        f = self._make_filter()
        texts, deltas = [], []
        for chunk in ["<think>I need", " to think", "</think>Here is", " the answer"]:
            t, r = f.feed(chunk)
            texts.append(t)
            deltas.append(r)
        texts.append(f.flush())
        assert "".join(texts) == "Here is the answer"
        assert "".join(deltas) == "I need to think"

    def test_opening_tag_split(self):
        """Opening <think> tag split across chunks."""
        f = self._make_filter()
        texts = []
        for chunk in ["Hello <th", "ink>hidden</think>world"]:
            t, _ = f.feed(chunk)
            texts.append(t)
        texts.append(f.flush())
        assert "".join(texts) == "Hello world"

    def test_closing_tag_split(self):
        """Closing </think> tag split across chunks."""
        f = self._make_filter()
        texts = []
        for chunk in ["<think>reasoning</th", "ink>visible"]:
            t, _ = f.feed(chunk)
            texts.append(t)
        texts.append(f.flush())
        assert "".join(texts) == "visible"

    def test_multiple_think_blocks(self):
        """Multiple <think> blocks in one message."""
        f = self._make_filter()
        text, rdelta = f.feed("<think>first</think>A<think>second</think>B")
        assert text == "AB"
        assert "first" in f.reasoning
        assert "second" in f.reasoning

    def test_text_before_think(self):
        """Text before the think block is preserved."""
        f = self._make_filter()
        text, _ = f.feed("Preamble <think>hidden</think>conclusion")
        assert text == "Preamble conclusion"

    def test_empty_think_block(self):
        """Empty <think></think> is stripped cleanly."""
        f = self._make_filter()
        text, _ = f.feed("<think></think>Answer")
        assert text == "Answer"

    def test_flush_partial_non_tag(self):
        """Partial '<' that isn't a tag should flush correctly."""
        f = self._make_filter()
        text, _ = f.feed("x < y")
        rest = f.flush()
        combined = text + rest
        assert "x < y" in combined or "x " in combined

    def test_reset_between_turns(self):
        """Filter can be re-instantiated between model turns."""
        from agent.executor import _ThinkFilter
        f1 = _ThinkFilter()
        f1.feed("<think>r1</think>A")
        assert f1.reasoning == "r1"

        f2 = _ThinkFilter()
        f2.feed("<think>r2</think>B")
        assert f2.reasoning == "r2"

    # --- reasoning_delta specific ---

    def test_delta_is_incremental(self):
        """Each feed() call returns only NEW reasoning, not cumulative."""
        f = self._make_filter()
        _, d1 = f.feed("<think>part1")
        assert d1 == "part1"
        _, d2 = f.feed(" part2")
        assert d2 == " part2"
        _, d3 = f.feed("</think>Answer")
        # d3 might be empty if no new reasoning was captured after closing tag
        assert "part1" not in d3  # must NOT contain previously captured content

    def test_delta_empty_for_normal_text(self):
        """No reasoning_delta when processing normal text."""
        f = self._make_filter()
        _, delta = f.feed("Just normal text")
        assert delta == ""

    def test_delta_across_open_and_close(self):
        """reasoning_delta captures content between <think> open and close."""
        f = self._make_filter()
        _, d1 = f.feed("<think>")
        assert d1 == ""  # no reasoning content yet, just the tag
        _, d2 = f.feed("actual reasoning")
        assert d2 == "actual reasoning"
        _, d3 = f.feed("</think>")
        assert d3 == ""  # closing tag, no new content

    def test_feed_returns_tuple(self):
        """feed() always returns a 2-tuple."""
        f = self._make_filter()
        result = f.feed("anything")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# K: _strip_model_tags — complete text stripping
# ═══════════════════════════════════════════════════════════════════════════════


class TestStripModelTags:
    """Verify _strip_model_tags removes model-specific XML tags from complete text."""

    def test_strips_think_block(self):
        from agent.executor import _strip_model_tags
        result = _strip_model_tags("<think>reasoning</think>The answer")
        assert result == "The answer"
        assert "<think>" not in result

    def test_strips_multiline_think(self):
        from agent.executor import _strip_model_tags
        text = "<think>\nLine 1\nLine 2\n</think>\nAnswer here"
        result = _strip_model_tags(text)
        assert "Line 1" not in result
        assert "Answer here" in result

    def test_no_tags(self):
        from agent.executor import _strip_model_tags
        assert _strip_model_tags("plain text") == "plain text"

    def test_strips_standalone_tags(self):
        from agent.executor import _strip_model_tags
        result = _strip_model_tags("before <think> stray tag after")
        assert "<think>" not in result

    def test_empty_string(self):
        from agent.executor import _strip_model_tags
        assert _strip_model_tags("") == ""


# ═══════════════════════════════════════════════════════════════════════════════
# K: _extract_reasoning
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractReasoning:
    """Verify _extract_reasoning captures think block content."""

    def test_extracts_single_block(self):
        from agent.executor import _extract_reasoning
        result = _extract_reasoning("<think>I should do X</think>Answer")
        assert result == "I should do X"

    def test_extracts_multiple_blocks(self):
        from agent.executor import _extract_reasoning
        result = _extract_reasoning("<think>part1</think>middle<think>part2</think>end")
        assert "part1" in result
        assert "part2" in result

    def test_no_think_blocks(self):
        from agent.executor import _extract_reasoning
        assert _extract_reasoning("plain text") == ""

    def test_empty_think_block(self):
        from agent.executor import _extract_reasoning
        assert _extract_reasoning("<think></think>text") == ""

    def test_multiline_reasoning(self):
        from agent.executor import _extract_reasoning
        text = "<think>\nStep 1: Do X\nStep 2: Do Y\n</think>Result"
        result = _extract_reasoning(text)
        assert "Step 1" in result
        assert "Step 2" in result


# ═══════════════════════════════════════════════════════════════════════════════
# K: Persistence stripping (source inspection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistenceStripping:
    """Verify _persist_messages strips <think> tags before saving."""

    def test_persist_calls_strip_model_tags(self):
        """_persist_messages must call _strip_model_tags on AIMessage content."""
        from agent import executor
        source = inspect.getsource(executor._persist_messages)
        assert "_strip_model_tags" in source

    def test_persist_extracts_reasoning(self):
        """_persist_messages must call _extract_reasoning for separate storage."""
        from agent import executor
        source = inspect.getsource(executor._persist_messages)
        assert "_extract_reasoning" in source

    def test_persist_stores_reasoning_part(self):
        """Persisted reasoning must use type='reasoning'."""
        from agent import executor
        source = inspect.getsource(executor._persist_messages)
        assert '"reasoning"' in source

    def test_persist_tool_result_includes_tool_name(self):
        """Persisted tool_result parts must include tool_name for frontend summary."""
        from agent import executor
        source = inspect.getsource(executor._persist_messages)
        assert '"tool_name"' in source
        # Ensure tool_name is extracted from ToolMessage
        assert 'getattr(msg, "name"' in source


# ═══════════════════════════════════════════════════════════════════════════════
# K: Streaming filter + reasoning_delta (source inspection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamingFilter:
    """Verify _stream_and_translate uses ThinkFilter and emits reasoning_delta."""

    def test_stream_uses_think_filter(self):
        """_stream_and_translate must instantiate _ThinkFilter."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert "_ThinkFilter" in source

    def test_stream_calls_feed(self):
        """_stream_and_translate must call think_filter.feed()."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert "think_filter.feed" in source

    def test_stream_resets_filter_on_tools_node(self):
        """_stream_and_translate must reset ThinkFilter when tools node fires."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert "think_filter = _ThinkFilter()" in source

    def test_stream_calls_flush(self):
        """_stream_and_translate must call think_filter.flush()."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert "think_filter.flush" in source

    def test_stream_emits_reasoning_delta(self):
        """_stream_and_translate must emit reasoning_delta SSE events."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert '"reasoning_delta"' in source

    def test_stream_reasoning_delta_has_content(self):
        """reasoning_delta event must include content field."""
        from agent import executor
        source = inspect.getsource(executor._stream_and_translate)
        assert "reasoning_delta" in source
        assert '"content": reasoning_delta' in source or '"content":' in source


# ═══════════════════════════════════════════════════════════════════════════════
# K: Frontmatter YAML quotes stripping
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrontmatterQuotes:
    """Verify parse_frontmatter strips surrounding YAML quotes from values."""

    def _parse(self, yaml_block: str) -> dict:
        from skills.package import parse_frontmatter
        return parse_frontmatter(f"---\n{yaml_block}\n---\nBody\n")

    def test_double_quoted_name(self):
        fm = self._parse('name: "doc"\ndescription: test')
        assert fm["name"] == "doc"

    def test_double_quoted_description(self):
        fm = self._parse('name: x\ndescription: "A tool for docs"')
        assert fm["description"] == "A tool for docs"

    def test_single_quoted_name(self):
        fm = self._parse("name: 'pdf'\ndescription: test")
        assert fm["name"] == "pdf"

    def test_unquoted_values_unchanged(self):
        fm = self._parse("name: doc\ndescription: A tool")
        assert fm["name"] == "doc"
        assert fm["description"] == "A tool"

    def test_quoted_version(self):
        fm = self._parse('name: x\ndescription: y\nversion: "1.2.3"')
        assert fm["version"] == "1.2.3"

    def test_quoted_tags(self):
        fm = self._parse('name: x\ndescription: y\ntags: ["doc", "pdf"]')
        assert fm["tags"] == ["doc", "pdf"]

    def test_quoted_icon(self):
        fm = self._parse('name: x\ndescription: y\nicon: "puzzle"')
        assert fm["icon"] == "puzzle"

    def test_mixed_quotes(self):
        """Some values quoted, some not."""
        fm = self._parse('name: "doc"\ndescription: A plain description\nversion: 1.0.0')
        assert fm["name"] == "doc"
        assert fm["description"] == "A plain description"
        assert fm["version"] == "1.0.0"

    def test_empty_quotes(self):
        """Empty quoted string should become empty."""
        fm = self._parse('name: ""\ndescription: test')
        assert fm["name"] == ""

    def test_mismatched_quotes_preserved(self):
        """Mismatched quotes should NOT be stripped."""
        fm = self._parse("name: \"doc'\ndescription: test")
        assert fm["name"] == "\"doc'"


# ═══════════════════════════════════════════════════════════════════════════════
# K: Migration 010 — strip quotes from existing skill data
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigration010:
    """Verify migration 010 exists and cleans quoted skill data."""

    def test_migration_file_exists(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "db", "alembic", "versions",
            "010_strip_skill_quotes.py",
        )
        assert os.path.isfile(path)

    def test_migration_revision(self):
        from db.alembic.versions import __path__ as versions_path
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(
            "m010",
            os.path.join(versions_path[0], "010_strip_skill_quotes.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "010"
        assert mod.down_revision == "009"

    def test_migration_strips_skills_name(self):
        """Migration must UPDATE skills.name to strip quotes."""
        with open(
            __import__("os").path.join(
                __import__("os").path.dirname(__file__),
                "..", "db", "alembic", "versions", "010_strip_skill_quotes.py",
            )
        ) as f:
            source = f.read()
        assert "UPDATE skills SET name" in source
        assert "TRIM" in source

    def test_migration_strips_skills_description(self):
        """Migration must UPDATE skills.description to strip quotes."""
        with open(
            __import__("os").path.join(
                __import__("os").path.dirname(__file__),
                "..", "db", "alembic", "versions", "010_strip_skill_quotes.py",
            )
        ) as f:
            source = f.read()
        assert "UPDATE skills SET description" in source

    def test_migration_strips_user_skills(self):
        """Migration must UPDATE user_skills.skill_name to strip quotes."""
        with open(
            __import__("os").path.join(
                __import__("os").path.dirname(__file__),
                "..", "db", "alembic", "versions", "010_strip_skill_quotes.py",
            )
        ) as f:
            source = f.read()
        assert "UPDATE user_skills SET skill_name" in source

    def test_expected_schema_version(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "016"
