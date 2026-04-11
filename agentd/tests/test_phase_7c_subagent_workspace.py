"""Phase 7C — child session workspace inheritance tests.

Focus:
- user file tools run against ctx.workspace_dir when it differs from ctx.session_dir
- child-only internal state still writes to ctx.session_dir
- runtime header reflects the effective working directory
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from tools.base import ToolContext


@pytest.fixture
def child_and_parent_dirs(tmp_path):
    parent_dir = tmp_path / "parent-session"
    child_dir = tmp_path / "child-session"
    parent_dir.mkdir()
    child_dir.mkdir()
    (parent_dir / "report.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (parent_dir / "notes.md").write_text("# Notes\nworkspace shared\n", encoding="utf-8")
    (child_dir / "child_only.txt").write_text("child\n", encoding="utf-8")
    return str(child_dir), str(parent_dir)


@pytest.fixture
def child_ctx(child_and_parent_dirs):
    child_dir, parent_dir = child_and_parent_dirs
    return ToolContext(
        user_id="u1",
        session_id="child-session",
        user_root=os.path.dirname(child_dir),
        session_dir=child_dir,
        workspace_dir=parent_dir,
        venv_bin="/tmp/venv/bin",
        publish=AsyncMock(),
    )


class TestChildWorkspaceInheritance:
    @pytest.mark.asyncio
    async def test_list_dir_reads_parent_workspace(self, child_ctx):
        from tools.list_dir import ListDirTool

        result = await ListDirTool().execute(child_ctx, path=".")

        assert result["is_error"] is False
        assert "report.txt" in result["output"]
        assert "child_only.txt" not in result["output"]

    @pytest.mark.asyncio
    async def test_glob_reads_parent_workspace(self, child_ctx):
        from tools.glob import GlobTool

        result = await GlobTool().execute(child_ctx, pattern="*.txt")

        assert result["is_error"] is False
        assert result["output"].strip() == "report.txt"

    @pytest.mark.asyncio
    async def test_glob_normalizes_symlinked_workspace_root(self, tmp_path):
        from tools.glob import GlobTool

        real_root = tmp_path / "real-parent"
        link_root = tmp_path / "linked-parent"
        real_root.mkdir()
        link_root.symlink_to(real_root, target_is_directory=True)
        (real_root / "sample.txt").write_text("hello\n", encoding="utf-8")

        ctx = ToolContext(
            user_id="u1",
            session_id="child-session",
            user_root=str(tmp_path),
            session_dir=str(tmp_path / "child-session"),
            workspace_dir=str(link_root),
            venv_bin="/tmp/venv/bin",
            publish=AsyncMock(),
        )
        os.makedirs(ctx.session_dir, exist_ok=True)

        result = await GlobTool().execute(ctx, pattern="*.txt")

        assert result["is_error"] is False
        assert result["output"].strip() == "sample.txt"

    @pytest.mark.asyncio
    async def test_grep_reads_parent_workspace(self, child_ctx):
        from tools.grep import GrepTool

        result = await GrepTool().execute(child_ctx, pattern="alpha", path=".")

        assert result["is_error"] is False
        assert "report.txt:1: alpha" in result["output"]

    @pytest.mark.asyncio
    async def test_file_write_writes_to_parent_workspace(self, child_ctx):
        from tools.file_write import FileWriteTool

        result = await FileWriteTool().execute(
            child_ctx,
            path="child_output.txt",
            content="from child\n",
        )

        assert result["is_error"] is False
        assert os.path.isfile(os.path.join(child_ctx.workspace_dir, "child_output.txt"))
        assert not os.path.exists(os.path.join(child_ctx.session_dir, "child_output.txt"))

    @pytest.mark.asyncio
    async def test_bash_uses_parent_workspace_as_cwd(self, child_ctx):
        from tools.bash import BashTool

        with patch("tools.bash.asyncio.create_subprocess_shell") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            result = await BashTool().execute(child_ctx, command="pwd")

            assert result["is_error"] is False
            assert mock_proc.call_args.kwargs["cwd"] == child_ctx.workspace_dir


class TestChildInternalStateBoundary:
    @pytest.mark.asyncio
    async def test_planning_stays_in_child_session_dir(self, child_ctx):
        from tools.planning import PlanningTool

        result = await PlanningTool().execute(
            child_ctx,
            task_title="Child plan",
            steps=[{"id": "s1", "title": "Inspect parent workspace", "status": "pending"}],
        )

        assert result["is_error"] is False
        plan_path = os.path.join(child_ctx.session_dir, ".agentd", "task_plan.json")
        assert os.path.isfile(plan_path)
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        assert plan["task"]["title"] == "Child plan"
        assert not os.path.exists(os.path.join(child_ctx.workspace_dir, ".agentd", "task_plan.json"))


class TestRuntimeHeader:
    def test_runtime_header_prefers_workspace_dir(self, tmp_path):
        from agent.runtime import build_system_prompt

        session_dir = str(tmp_path / "child-session")
        workspace_dir = str(tmp_path / "parent-session")
        os.makedirs(session_dir, exist_ok=True)
        os.makedirs(workspace_dir, exist_ok=True)

        prompt, _ = build_system_prompt(
            agent_id="build",
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            user_root=str(tmp_path),
            model_id="test-model",
            session_id="child-session",
        )

        assert f"- Working directory: {workspace_dir}" in prompt
        assert f"- Session state directory: {session_dir}" in prompt
