"""Phase 7D — unified runtime environment boundary tests."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.env import register_skill_scripts
from tools.base import ToolContext


@pytest.fixture
def session_dir(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    return str(session_dir)


@pytest.fixture
def user_venv_bin(tmp_path):
    env_bin = tmp_path / "user-env" / "bin"
    env_bin.mkdir(parents=True, exist_ok=True)
    return str(env_bin)


@pytest.fixture
def ctx(session_dir, user_venv_bin):
    return ToolContext(
        user_id="u1",
        session_id="s1",
        user_root=os.path.dirname(os.path.dirname(session_dir)),
        session_dir=session_dir,
        workspace_dir=session_dir,
        venv_bin=user_venv_bin,
        publish=AsyncMock(),
    )


class TestRuntimeEnvResolver:
    def test_command_defaults_to_user_env(self, ctx, user_venv_bin):
        from agent.runtime_env import resolve_command_execution

        execution = resolve_command_execution(ctx, "echo hello")

        assert execution.env_kind == "user"
        assert execution.env_bin == user_venv_bin
        assert execution.python_bin == os.path.join(user_venv_bin, "python")
        assert execution.workdir == ctx.workspace_dir

    def test_command_uses_skill_env_when_registered(self, ctx, session_dir, tmp_path):
        from agent.runtime_env import resolve_command_execution

        skill_env_bin = tmp_path / "catalog" / "skill" / ".venv" / "bin"
        skill_env_bin.mkdir(parents=True, exist_ok=True)
        register_skill_scripts(
            session_dir,
            "pdf-rename",
            "1.1.0",
            str(skill_env_bin),
            ["scripts/pdf_extract_text.py"],
        )

        execution = resolve_command_execution(
            ctx,
            "python scripts/pdf_extract_text.py claim.pdf",
        )

        assert execution.env_kind == "skill"
        assert execution.env_bin == str(skill_env_bin)
        assert execution.skill_name == "pdf-rename"
        assert execution.skill_version == "1.1.0"

    def test_command_uses_service_env_for_isolated_cli(self, ctx):
        from agent.runtime_env import resolve_command_execution

        service = SimpleNamespace(
            name="employee-risk-cli",
            env_kind="isolated",
        )

        execution = resolve_command_execution(
            ctx,
            "/opt/employee-risk/bin/employee-risk-cli --help",
            service=service,
            workdir="/opt/employee-risk/bin",
        )

        assert execution.env_kind == "service"
        assert execution.service_name == "employee-risk-cli"
        assert execution.env_bin == ""
        assert execution.python_bin == ""
        assert execution.workdir == "/opt/employee-risk/bin"


class TestDetachedRuntimePersistence:
    @pytest.mark.asyncio
    async def test_launch_detached_persists_effective_env_metadata(self, ctx, session_dir, user_venv_bin):
        from agent.tasks import read_task_meta
        from tools.launch_detached import LaunchDetachedProcessTool

        tool = LaunchDetachedProcessTool()
        process = MagicMock(pid=4321, stdout=AsyncMock(), stderr=AsyncMock())
        closed_coroutines: list = []

        def _close_background_task(coro):
            closed_coroutines.append(coro)
            coro.close()
            return MagicMock()

        with (
            patch.object(tool, "_create_db_record", new=AsyncMock()),
            patch("tools.launch_detached._update_db_pid", new=AsyncMock()),
            patch("tools.launch_detached.asyncio.create_task", side_effect=_close_background_task),
            patch(
                "tools.launch_detached.asyncio.create_subprocess_shell",
                new=AsyncMock(return_value=process),
            ) as mock_proc,
        ):
            result = await tool.execute(
                ctx,
                title="Detached echo",
                command="echo hello",
            )

        payload = json.loads(result["output"])
        meta = read_task_meta(session_dir, payload["task_id"])
        assert meta["effective_env_kind"] == "user"
        assert meta["effective_workdir"] == session_dir
        assert meta["effective_env_bin"] == user_venv_bin
        assert meta["effective_python_bin"] == os.path.join(user_venv_bin, "python")

        called_env = mock_proc.await_args.kwargs["env"]
        assert called_env["AGENTD_ENV_KIND"] == "user"
        assert called_env["AGENTD_EFFECTIVE_WORKDIR"] == session_dir
        assert called_env["PATH"].startswith(user_venv_bin)
        assert len(closed_coroutines) == 1
