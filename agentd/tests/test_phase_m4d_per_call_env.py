"""Phase M4-D — Per-Call Skill Env Binding tests.

Tests cover:
- BashTool: skill env PATH injection when command matches registered script
- BashTool: fallback to user venv when no match
- ScriptTool: skill env python when script matches registered entry
- ScriptTool: fallback to user venv python when no match
- Both tools: multiple skills resolve to correct envs
"""

import asyncio
import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from tools.base import ToolContext
from tools.bash import BashTool
from tools.script import ScriptTool
from skills.env import register_skill_scripts, read_skill_envs


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def session_dir(tmp_path):
    sd = str(tmp_path / "sessions" / "test-session")
    os.makedirs(sd, exist_ok=True)
    return sd


@pytest.fixture
def user_venv_bin(tmp_path):
    vb = str(tmp_path / "user-venv" / "bin")
    os.makedirs(vb, exist_ok=True)
    return vb


@pytest.fixture
def skill_env_bin(tmp_path):
    eb = str(tmp_path / "catalog" / "pdf-rename" / "1.1.0" / ".venv" / "bin")
    os.makedirs(eb, exist_ok=True)
    return eb


@pytest.fixture
def ctx(session_dir, user_venv_bin):
    return ToolContext(
        user_id="u1",
        session_id="s1",
        user_root=str(os.path.dirname(session_dir)),
        session_dir=session_dir,
        workspace_dir=session_dir,
        venv_bin=user_venv_bin,
        publish=lambda *a, **kw: None,
    )


def _register_skill(session_dir, skill_env_bin, skill_name="pdf-rename",
                     version="1.1.0", scripts=None):
    """Register skill scripts in the session env mapping."""
    scripts = scripts or ["scripts/pdf_extract_text.py"]
    register_skill_scripts(session_dir, skill_name, version, skill_env_bin, scripts)


# ── Test: BashTool per-call env ──────────────────────────────────────────────


class TestBashToolPerCallEnv:

    @pytest.mark.asyncio
    async def test_skill_env_injected_when_command_matches(
        self, session_dir, user_venv_bin, skill_env_bin, ctx,
    ):
        """Command referencing a registered script → skill env PATH."""
        _register_skill(session_dir, skill_env_bin)

        tool = BashTool()
        command = "python scripts/pdf_extract_text.py claim.pdf --chars 300"

        with patch("tools.bash.asyncio.create_subprocess_shell") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, command=command)

            called_cmd = mock_proc.call_args[0][0]
            called_env = mock_proc.call_args.kwargs["env"]
            assert called_cmd == command
            assert called_env["PATH"].startswith(skill_env_bin)
            assert user_venv_bin not in called_env["PATH"]
            assert called_env["AGENTD_ENV_KIND"] == "skill"

    @pytest.mark.asyncio
    async def test_user_env_when_no_match(
        self, session_dir, user_venv_bin, skill_env_bin, ctx,
    ):
        """Command not referencing any registered script → user venv PATH."""
        _register_skill(session_dir, skill_env_bin)

        tool = BashTool()
        command = "ls -la"

        with patch("tools.bash.asyncio.create_subprocess_shell") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"files\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, command=command)

            called_env = mock_proc.call_args.kwargs["env"]
            assert called_env["PATH"].startswith(user_venv_bin)
            assert skill_env_bin not in called_env["PATH"]
            assert called_env["AGENTD_ENV_KIND"] == "user"

    @pytest.mark.asyncio
    async def test_user_env_when_no_mapping_file(
        self, session_dir, user_venv_bin, ctx,
    ):
        """No skill_envs.json at all → user venv PATH."""
        tool = BashTool()
        command = "python scripts/pdf_extract_text.py"

        with patch("tools.bash.asyncio.create_subprocess_shell") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, command=command)

            called_env = mock_proc.call_args.kwargs["env"]
            assert called_env["PATH"].startswith(user_venv_bin)

    @pytest.mark.asyncio
    async def test_multi_skill_correct_resolution(
        self, tmp_path, session_dir, user_venv_bin, ctx,
    ):
        """Two skills registered → each command resolves to correct env."""
        env1 = str(tmp_path / "cat" / "pdf" / ".venv" / "bin")
        env2 = str(tmp_path / "cat" / "ocr" / ".venv" / "bin")
        os.makedirs(env1, exist_ok=True)
        os.makedirs(env2, exist_ok=True)

        _register_skill(session_dir, env1, "pdf-rename", "1.1.0", ["scripts/split.py"])
        _register_skill(session_dir, env2, "ocr", "0.1.0", ["scripts/scan.py"])

        tool = BashTool()

        with patch("tools.bash.asyncio.create_subprocess_shell") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            # Command 1: pdf skill
            await tool.execute(ctx, command="python scripts/split.py plan.json")
            called1 = mock_proc.call_args.kwargs["env"]["PATH"]
            assert called1.startswith(env1)

            # Command 2: ocr skill
            await tool.execute(ctx, command="python scripts/scan.py img.png")
            called2 = mock_proc.call_args.kwargs["env"]["PATH"]
            assert called2.startswith(env2)

            # Command 3: unrelated
            await tool.execute(ctx, command="echo hello")
            called3 = mock_proc.call_args.kwargs["env"]["PATH"]
            assert called3.startswith(user_venv_bin)


# ── Test: ScriptTool per-call env ────────────────────────────────────────────


class TestScriptToolPerCallEnv:

    @pytest.mark.asyncio
    async def test_skill_env_via_scripts_prefix(
        self, session_dir, user_venv_bin, skill_env_bin, ctx,
    ):
        """M4-C materializes as scripts/<name>; script tool receives bare <name>."""
        # This is the real M4-C convention: key = "scripts/env_probe.py"
        register_skill_scripts(
            session_dir, "env-proof", "0.1.0", skill_env_bin,
            ["scripts/env_probe.py"],
        )

        tool = ScriptTool()

        with patch("tools.script.asyncio.create_subprocess_exec") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"result\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            # Script tool receives bare filename, should still resolve
            await tool.execute(ctx, filename="env_probe.py", content="print('hi')")

            called_args = mock_proc.call_args[0]
            python_path = called_args[0]
            assert skill_env_bin in python_path

    @pytest.mark.asyncio
    async def test_skill_env_via_bare_basename(
        self, session_dir, user_venv_bin, skill_env_bin, ctx,
    ):
        """Fallback: bare basename key also works."""
        register_skill_scripts(
            session_dir, "pdf-rename", "1.1.0", skill_env_bin,
            ["analysis.py"],
        )

        tool = ScriptTool()

        with patch("tools.script.asyncio.create_subprocess_exec") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"result\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, filename="analysis.py", content="print('hi')")

            called_args = mock_proc.call_args[0]
            python_path = called_args[0]
            assert skill_env_bin in python_path

    @pytest.mark.asyncio
    async def test_user_env_python_when_no_match(
        self, session_dir, user_venv_bin, skill_env_bin, ctx,
    ):
        """Script name not registered → user venv python."""
        _register_skill(session_dir, skill_env_bin, scripts=["scripts/other.py"])

        tool = ScriptTool()

        with patch("tools.script.asyncio.create_subprocess_exec") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"result\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, filename="my_script.py", content="print('hi')")

            called_args = mock_proc.call_args[0]
            python_path = called_args[0]
            assert user_venv_bin in python_path
            assert skill_env_bin not in python_path

    @pytest.mark.asyncio
    async def test_user_env_when_no_mapping(
        self, session_dir, user_venv_bin, ctx,
    ):
        """No mapping file → user venv python."""
        tool = ScriptTool()

        with patch("tools.script.asyncio.create_subprocess_exec") as mock_proc:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", None)
            mock_process.returncode = 0
            mock_proc.return_value = mock_process

            await tool.execute(ctx, filename="test.py", content="print(1)")

            called_args = mock_proc.call_args[0]
            python_path = called_args[0]
            assert user_venv_bin in python_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
