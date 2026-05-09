"""Tests for Phase E tools — list_dir, glob, grep, file_edit.

Covers:
- ListDirTool: tree listing, depth limiting, hidden file filtering, path validation
- GlobTool: pattern matching, ** support, max results
- GrepTool: regex search, line numbers, include filter, binary skip
- FileEditTool: find-and-replace, uniqueness check, path validation
- Registry: new tools registered with correct permissions
"""

import os

import pytest

from tools.base import ToolContext


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with sample files for testing."""
    # Create directory structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    print('hello')\n")
    (tmp_path / "src" / "utils.py").write_text("def helper():\n    return 42\n")
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "deep.py").write_text("# deep module\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# Project\nSome docs here.\n")
    (tmp_path / "config.json").write_text('{"key": "value"}\n')
    (tmp_path / ".hidden_dir").mkdir()
    (tmp_path / ".hidden_dir" / "secret.txt").write_text("secret\n")
    (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    return tmp_path


@pytest.fixture
def ctx(workspace):
    """Create a ToolContext pointing to the workspace."""
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=str(workspace),
        session_dir=str(workspace),
        workspace_dir=str(workspace),
        venv_bin=str(workspace / ".venv" / "bin"),
        publish=None,
    )


# ── ListDirTool ──────────────────────────────────────────────────────────────


class TestListDirTool:
    @pytest.fixture
    def tool(self):
        from tools.list_dir import ListDirTool
        return ListDirTool()

    @pytest.mark.asyncio
    async def test_basic_listing(self, tool, ctx):
        result = await tool.execute(ctx)
        assert result["is_error"] is False
        output = result["output"]
        assert "src/" in output
        assert "docs/" in output
        assert "config.json" in output

    @pytest.mark.asyncio
    async def test_hidden_dirs_excluded(self, tool, ctx):
        result = await tool.execute(ctx)
        assert ".hidden_dir" not in result["output"]

    @pytest.mark.asyncio
    async def test_gitignore_included(self, tool, ctx):
        result = await tool.execute(ctx)
        assert ".gitignore" in result["output"]

    @pytest.mark.asyncio
    async def test_subdirectory(self, tool, ctx):
        result = await tool.execute(ctx, path="src")
        assert result["is_error"] is False
        assert "main.py" in result["output"]
        assert "sub/" in result["output"]

    @pytest.mark.asyncio
    async def test_quoted_current_dir_is_normalized(self, tool, ctx):
        result = await tool.execute(ctx, path='"."')
        assert result["is_error"] is False
        assert "src/" in result["output"]

    @pytest.mark.asyncio
    async def test_null_like_path_is_current_dir(self, tool, ctx):
        result = await tool.execute(ctx, path="null")
        assert result["is_error"] is False
        assert "src/" in result["output"]

    @pytest.mark.asyncio
    async def test_workspace_absolute_path_is_normalized(self, tool, ctx, workspace):
        result = await tool.execute(ctx, path=str(workspace))
        assert result["is_error"] is False
        assert "config.json" in result["output"]

    @pytest.mark.asyncio
    async def test_depth_limiting(self, tool, ctx):
        result = await tool.execute(ctx, max_depth=1)
        assert result["is_error"] is False
        # Should show dirs but not recurse into them
        assert "src/" in result["output"]
        assert "main.py" not in result["output"]

    @pytest.mark.asyncio
    async def test_not_a_directory(self, tool, ctx):
        result = await tool.execute(ctx, path="config.json")
        assert result["is_error"] is True
        assert "Not a directory" in result["output"]

    @pytest.mark.asyncio
    async def test_empty_directory(self, tool, ctx, workspace):
        (workspace / "empty").mkdir()
        result = await tool.execute(ctx, path="empty")
        assert result["is_error"] is False
        assert result["output"] == "(empty directory)"

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, tool, ctx):
        result = await tool.execute(ctx, path="../../../etc")
        assert result["is_error"] is True
        assert "escape" in result["output"].lower() or "permission" in result["output"].lower()


# ── GlobTool ────────────────────────────────────────────────────────────────


class TestGlobTool:
    @pytest.fixture
    def tool(self):
        from tools.glob import GlobTool
        return GlobTool()

    @pytest.mark.asyncio
    async def test_recursive_python_glob(self, tool, ctx):
        result = await tool.execute(ctx, pattern="**/*.py")
        assert result["is_error"] is False
        output = result["output"]
        assert "src/main.py" in output
        assert "src/utils.py" in output
        assert "src/sub/deep.py" in output

    @pytest.mark.asyncio
    async def test_single_level_glob(self, tool, ctx):
        result = await tool.execute(ctx, pattern="*.json")
        assert result["is_error"] is False
        assert "config.json" in result["output"]

    @pytest.mark.asyncio
    async def test_no_matches(self, tool, ctx):
        result = await tool.execute(ctx, pattern="**/*.xyz")
        assert result["is_error"] is False
        assert "No files matched" in result["output"]

    @pytest.mark.asyncio
    async def test_subdirectory_search(self, tool, ctx):
        result = await tool.execute(ctx, pattern="*.py", path="src")
        assert result["is_error"] is False
        assert "main.py" in result["output"]

    @pytest.mark.asyncio
    async def test_quoted_pattern_and_empty_path_are_normalized(self, tool, ctx):
        result = await tool.execute(ctx, pattern='"*.json"', path='""')
        assert result["is_error"] is False
        assert "config.json" in result["output"]

    @pytest.mark.asyncio
    async def test_null_like_path_is_current_dir(self, tool, ctx):
        result = await tool.execute(ctx, pattern="*.json", path="undefined")
        assert result["is_error"] is False
        assert "config.json" in result["output"]

    @pytest.mark.asyncio
    async def test_hidden_files_excluded(self, tool, ctx):
        result = await tool.execute(ctx, pattern="**/*")
        assert result["is_error"] is False
        assert "secret.txt" not in result["output"]

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, tool, ctx):
        result = await tool.execute(ctx, pattern="*.py", path="../..")
        assert result["is_error"] is True


# ── GrepTool ────────────────────────────────────────────────────────────────


class TestGrepTool:
    @pytest.fixture
    def tool(self):
        from tools.grep import GrepTool
        return GrepTool()

    @pytest.mark.asyncio
    async def test_basic_search(self, tool, ctx):
        result = await tool.execute(ctx, pattern="def main")
        assert result["is_error"] is False
        output = result["output"]
        assert "src/main.py:1:" in output
        assert "def main" in output

    @pytest.mark.asyncio
    async def test_regex_search(self, tool, ctx):
        result = await tool.execute(ctx, pattern=r"return \d+")
        assert result["is_error"] is False
        assert "src/utils.py" in result["output"]
        assert "42" in result["output"]

    @pytest.mark.asyncio
    async def test_no_matches(self, tool, ctx):
        result = await tool.execute(ctx, pattern="nonexistent_string_xyz")
        assert result["is_error"] is False
        assert "No matches found" in result["output"]

    @pytest.mark.asyncio
    async def test_include_filter(self, tool, ctx):
        result = await tool.execute(ctx, pattern=".", include="*.md")
        assert result["is_error"] is False
        assert "docs/readme.md" in result["output"]
        # Should not include .py files
        assert "main.py" not in result["output"]

    @pytest.mark.asyncio
    async def test_quoted_args_are_normalized(self, tool, ctx):
        result = await tool.execute(ctx, pattern='"Project"', path='"docs"', include='"*.md"')
        assert result["is_error"] is False
        assert "docs/readme.md" in result["output"]

    @pytest.mark.asyncio
    async def test_null_like_path_is_current_dir_but_pattern_remains_literal(self, tool, ctx, workspace):
        (workspace / "null.txt").write_text("literal null token\n")
        result = await tool.execute(ctx, pattern="null", path="None", include="*.txt")
        assert result["is_error"] is False
        assert "null.txt" in result["output"]

    @pytest.mark.asyncio
    async def test_prompt_contaminated_path_is_rejected(self, tool, ctx):
        result = await tool.execute(ctx, pattern="Project", path="path: docs\nthen grep")
        assert result["is_error"] is True
        assert "TOOL_ARGUMENT_VALIDATION_ERROR" in result["output"]

    @pytest.mark.asyncio
    async def test_search_single_file(self, tool, ctx):
        result = await tool.execute(ctx, pattern="hello", path="src/main.py")
        assert result["is_error"] is False
        assert "hello" in result["output"]

    @pytest.mark.asyncio
    async def test_invalid_regex(self, tool, ctx):
        result = await tool.execute(ctx, pattern="[invalid")
        assert result["is_error"] is True
        assert "Invalid regex" in result["output"]

    @pytest.mark.asyncio
    async def test_path_not_found(self, tool, ctx):
        result = await tool.execute(ctx, pattern="test", path="nonexistent")
        assert result["is_error"] is True
        assert "not found" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, tool, ctx):
        result = await tool.execute(ctx, pattern="test", path="../..")
        assert result["is_error"] is True


# ── FileEditTool ─────────────────────────────────────────────────────────────


class TestFileEditTool:
    @pytest.fixture
    def tool(self):
        from tools.file_edit import FileEditTool
        return FileEditTool()

    @pytest.mark.asyncio
    async def test_basic_edit(self, tool, ctx, workspace):
        target = workspace / "edit_test.py"
        target.write_text("def foo():\n    return 1\n")

        result = await tool.execute(ctx, path="edit_test.py", old_text="return 1", new_text="return 2")
        assert result["is_error"] is False
        assert "replaced 1 occurrence" in result["output"]

        # Verify file content
        content = target.read_text()
        assert "return 2" in content
        assert "return 1" not in content

    @pytest.mark.asyncio
    async def test_preserves_surrounding_content(self, tool, ctx, workspace):
        target = workspace / "preserve.py"
        original = "# header\ndef foo():\n    return 1\n# footer\n"
        target.write_text(original)

        await tool.execute(ctx, path="preserve.py", old_text="return 1", new_text="return 2")

        content = target.read_text()
        assert content == "# header\ndef foo():\n    return 2\n# footer\n"

    @pytest.mark.asyncio
    async def test_old_text_not_found(self, tool, ctx, workspace):
        target = workspace / "nf.py"
        target.write_text("hello world\n")

        result = await tool.execute(ctx, path="nf.py", old_text="goodbye", new_text="hi")
        assert result["is_error"] is True
        assert "not found" in result["output"]

    @pytest.mark.asyncio
    async def test_multiple_matches_rejected(self, tool, ctx, workspace):
        target = workspace / "multi.py"
        target.write_text("x = 1\ny = 1\nz = 1\n")

        result = await tool.execute(ctx, path="multi.py", old_text="= 1", new_text="= 2")
        assert result["is_error"] is True
        assert "3 times" in result["output"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tool, ctx):
        result = await tool.execute(ctx, path="nonexistent.py", old_text="x", new_text="y")
        assert result["is_error"] is True
        assert "not found" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_path_escape_rejected(self, tool, ctx):
        result = await tool.execute(ctx, path="../../etc/passwd", old_text="x", new_text="y")
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_multiline_edit(self, tool, ctx, workspace):
        target = workspace / "ml.py"
        target.write_text("def foo():\n    x = 1\n    y = 2\n    return x + y\n")

        old = "    x = 1\n    y = 2"
        new = "    x = 10\n    y = 20"
        result = await tool.execute(ctx, path="ml.py", old_text=old, new_text=new)
        assert result["is_error"] is False

        content = target.read_text()
        assert "x = 10" in content
        assert "y = 20" in content


# ── Registry integration ────────────────────────────────────────────────────


class TestRegistryPhaseE:
    def test_new_tools_registered(self):
        from tools.registry import get_registry
        registry = get_registry()
        for name in ("list_dir", "glob", "grep", "file_edit"):
            assert registry.get(name) is not None, f"Tool '{name}' not registered"

    def test_read_only_tools_auto_allow(self):
        from tools.registry import get_registry
        registry = get_registry()
        assert registry.default_permission("list_dir") == "allow"
        assert registry.default_permission("glob") == "allow"
        assert registry.default_permission("grep") == "allow"

    def test_file_edit_requires_approval(self):
        from tools.registry import get_registry
        registry = get_registry()
        assert registry.default_permission("file_edit") == "ask"

    def test_hitl_includes_file_edit(self):
        from agent.runtime import _HITL_INTERRUPT_ON
        assert "file_edit" in _HITL_INTERRUPT_ON
        assert "list_dir" not in _HITL_INTERRUPT_ON
        assert "glob" not in _HITL_INTERRUPT_ON
        assert "grep" not in _HITL_INTERRUPT_ON


# ── Policy rule building for file_edit ──────────────────────────────────────


class TestFileEditPolicyRule:
    def test_file_edit_rule(self):
        from permission.router import _build_policy_rule
        rule = _build_policy_rule("file_edit", {"path": "x.py", "old_text": "a", "new_text": "b"})
        assert rule is not None
        assert rule.tool == "file_edit"
        assert rule.match["kind"] == "any_path_within_session"
