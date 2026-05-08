"""Tests for Phase G1 — workspace semantic tightening & internal path isolation.

Covers:
  - is_internal_path() classification
  - workspace/tree excludes .agentd/
  - workspace API rejects .agentd/ paths (download, file, meta, upload)
  - file tools reject .agentd/ paths (file_read, file_write, file_edit)
  - normal user files still work through tools
"""

import os

import pytest

from workspace.manager import (
    ensure_user_root,
    get_session_dir,
    is_internal_path,
    validate_path,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def user_root(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    return root


@pytest.fixture
def session_dir(user_root):
    sd = get_session_dir(user_root, "test-session")
    # Create .agentd/ with system files
    agentd_dir = os.path.join(sd, ".agentd")
    os.makedirs(agentd_dir, exist_ok=True)
    with open(os.path.join(agentd_dir, "task_plan.json"), "w") as f:
        f.write('{"active": true}')
    with open(os.path.join(agentd_dir, "session_policy.json"), "w") as f:
        f.write('{"mode": "default"}')
    # Create a normal user file
    with open(os.path.join(sd, "hello.txt"), "w") as f:
        f.write("Hello, world!")
    # Create a normal dotfile (.env) — should NOT be treated as internal
    with open(os.path.join(sd, ".env"), "w") as f:
        f.write("KEY=value")
    return sd


# ── Test: is_internal_path() ─────────────────────────────────────────────────


class TestIsInternalPath:
    def test_agentd_dir(self):
        assert is_internal_path(".agentd") is True

    def test_agentd_subpath(self):
        assert is_internal_path(".agentd/task_plan.json") is True

    def test_agentd_nested(self):
        assert is_internal_path(".agentd/sub/deep/file.txt") is True

    def test_agentd_with_leading_slash(self):
        assert is_internal_path("/.agentd/task_plan.json") is True

    def test_normal_file(self):
        assert is_internal_path("hello.txt") is False

    def test_normal_subdir(self):
        assert is_internal_path("src/main.py") is False

    def test_dotenv_not_internal(self):
        """Regular dotfiles like .env are NOT internal system paths."""
        assert is_internal_path(".env") is False

    def test_gitignore_not_internal(self):
        assert is_internal_path(".gitignore") is False

    def test_relative_traversal_to_agentd(self):
        """Path that normalises into .agentd should be caught."""
        assert is_internal_path("foo/../.agentd/task_plan.json") is True

    def test_empty_path(self):
        assert is_internal_path("") is False
        assert is_internal_path(".") is False


# ── Test: workspace/tree excludes .agentd/ ───────────────────────────────────


class TestTreeExcludesInternal:
    def test_tree_hides_agentd(self, session_dir):
        from workspace.router import _build_tree

        tree = _build_tree(session_dir)
        names = [node["name"] for node in tree]
        assert ".agentd" not in names

    def test_tree_shows_normal_file(self, session_dir):
        from workspace.router import _build_tree

        tree = _build_tree(session_dir)
        names = [node["name"] for node in tree]
        assert "hello.txt" in names

    def test_tree_hides_other_dotfiles(self, session_dir):
        """Current display policy still hides dotfiles like .env."""
        from workspace.router import _build_tree

        tree = _build_tree(session_dir)
        names = [node["name"] for node in tree]
        # .env is hidden by dotfile display policy (separate from access isolation)
        assert ".env" not in names


# ── Test: workspace API rejects .agentd/ paths ──────────────────────────────


class TestApiRejectsInternal:
    def test_reject_internal_helper(self):
        from fastapi import HTTPException
        from workspace.router import _reject_internal

        with pytest.raises(HTTPException) as exc_info:
            _reject_internal(".agentd/task_plan.json")
        assert exc_info.value.status_code == 400
        assert "internal system directory" in str(exc_info.value.detail["message"])

    def test_reject_internal_dir_only(self):
        from fastapi import HTTPException
        from workspace.router import _reject_internal

        with pytest.raises(HTTPException):
            _reject_internal(".agentd")

    def test_normal_path_passes(self):
        from workspace.router import _reject_internal

        # Should not raise
        _reject_internal("hello.txt")
        _reject_internal("src/main.py")


# ── Test: file tools reject .agentd/ paths ──────────────────────────────────


class TestFileToolsRejectInternal:
    @pytest.fixture
    def tool_ctx(self, user_root, session_dir):
        from tools.base import ToolContext
        return ToolContext(
            user_id="u1",
            session_id="test-session",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )

    @pytest.mark.asyncio
    async def test_file_read_rejects_agentd(self, tool_ctx):
        from tools.file_read import FileReadTool
        tool = FileReadTool()
        result = await tool.execute(tool_ctx, path=".agentd/task_plan.json")
        assert result["is_error"] is True
        assert "internal system directory" in result["output"]

    @pytest.mark.asyncio
    async def test_file_write_rejects_agentd(self, tool_ctx):
        from tools.file_write import FileWriteTool
        tool = FileWriteTool()
        result = await tool.execute(tool_ctx, path=".agentd/task_plan.json", content="hacked")
        assert result["is_error"] is True
        assert "internal system directory" in result["output"]

    @pytest.mark.asyncio
    async def test_file_edit_rejects_agentd(self, tool_ctx):
        from tools.file_edit import FileEditTool
        tool = FileEditTool()
        result = await tool.execute(
            tool_ctx,
            path=".agentd/session_policy.json",
            old_text='"mode": "default"',
            new_text='"mode": "hacked"',
        )
        assert result["is_error"] is True
        assert "internal system directory" in result["output"]

    @pytest.mark.asyncio
    async def test_file_read_normal_file_works(self, tool_ctx, session_dir):
        from tools.file_read import FileReadTool
        tool = FileReadTool()
        result = await tool.execute(tool_ctx, path="hello.txt")
        assert result["is_error"] is False
        assert "Hello, world!" in result["output"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename, payload",
        [
            ("sample.docx", b"PK\x03\x04fake office zip"),
            ("sample.xlsx", b"PK\x03\x04fake workbook zip"),
            ("sample.pdf", b"%PDF-1.7\nfake pdf"),
            ("sample.png", b"\x89PNG\r\n\x1a\nfake png"),
            ("sample.zip", b"PK\x03\x04fake zip"),
        ],
    )
    async def test_file_read_rejects_structured_binary_extensions(
        self,
        tool_ctx,
        session_dir,
        filename,
        payload,
    ):
        from tools.file_read import FileReadTool

        with open(os.path.join(session_dir, filename), "wb") as f:
            f.write(payload)

        tool = FileReadTool()
        result = await tool.execute(tool_ctx, path=filename)

        assert result["is_error"] is True
        assert "plain text" in result["output"]
        assert "file_inspect" in result["output"]

    @pytest.mark.asyncio
    async def test_file_read_rejects_binary_disguised_as_text(self, tool_ctx, session_dir):
        from tools.file_read import FileReadTool

        with open(os.path.join(session_dir, "fake.txt"), "wb") as f:
            f.write(b"hello\x00\x00\x00\x00world")

        tool = FileReadTool()
        result = await tool.execute(tool_ctx, path="fake.txt")

        assert result["is_error"] is True
        assert "plain text" in result["output"]

    @pytest.mark.asyncio
    async def test_file_read_allows_json_csv_and_py_text(self, tool_ctx, session_dir):
        from tools.file_read import FileReadTool

        files = {
            "data.json": '{"ok": true}\n',
            "rows.csv": "name,value\na,1\n",
            "script.py": "print('ok')\n",
        }
        for filename, content in files.items():
            with open(os.path.join(session_dir, filename), "w", encoding="utf-8") as f:
                f.write(content)

        tool = FileReadTool()
        for filename, content in files.items():
            result = await tool.execute(tool_ctx, path=filename)
            assert result["is_error"] is False
            assert result["output"] == content

    @pytest.mark.asyncio
    async def test_file_write_normal_file_works(self, tool_ctx, session_dir):
        from tools.file_write import FileWriteTool
        tool = FileWriteTool()
        result = await tool.execute(tool_ctx, path="output.txt", content="test content")
        assert result["is_error"] is False
        assert os.path.isfile(os.path.join(session_dir, "output.txt"))

    @pytest.mark.asyncio
    async def test_file_edit_normal_file_works(self, tool_ctx, session_dir):
        from tools.file_edit import FileEditTool
        tool = FileEditTool()
        result = await tool.execute(
            tool_ctx,
            path="hello.txt",
            old_text="Hello, world!",
            new_text="Hello, AgentD!",
        )
        assert result["is_error"] is False
        with open(os.path.join(session_dir, "hello.txt")) as f:
            assert f.read() == "Hello, AgentD!"


# ── Test: .agentd/ system files remain accessible to internal tools ─────────


class TestInternalToolsStillWork:
    def test_validate_path_still_allows_agentd(self, session_dir):
        """validate_path itself does not block .agentd — only is_internal_path does.

        Internal system tools (planning, todo_update) use validate_path directly
        without the is_internal_path guard, so they continue to work.
        """
        abs_path = validate_path(session_dir, ".agentd/task_plan.json")
        assert os.path.isfile(abs_path)
