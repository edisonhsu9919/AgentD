"""Phase M4-C — Skill Load Materialization & Env Mapping tests.

Tests cover:
- skill load materializes scripts to session_dir/scripts/
- skill load registers env mappings when catalog env exists
- skill load skips env mapping when no catalog env
- skill load without scripts dir — no materialization
- _materialize_skill_scripts handles errors gracefully
- Multiple skill loads accumulate scripts and mappings
- Existing skill load behavior preserved (content, version, prefix)
"""

import os
import shutil
from unittest.mock import patch, AsyncMock

import pytest

from tools.skill import SkillTool
from tools.base import ToolContext
from skills.filesystem import get_skills_dir
from skills.env import read_skill_envs


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Set workspace root to a temp directory."""
    from core import config
    monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def user_root(tmp_workspace):
    root = os.path.join(tmp_workspace, "test-user")
    os.makedirs(os.path.join(root, "skills"), exist_ok=True)
    return root


@pytest.fixture
def session_dir(user_root):
    sd = os.path.join(user_root, "sessions", "test-session")
    os.makedirs(sd, exist_ok=True)
    return sd


@pytest.fixture
def ctx(user_root, session_dir):
    return ToolContext(
        user_id="u1",
        session_id="s1",
        user_root=user_root,
        session_dir=session_dir,
        venv_bin="/tmp/venv/bin",
        publish=lambda *a, **kw: None,
    )


def _create_user_skill(user_root, name, version="1.0.0", desc="A test skill",
                        with_scripts=True, script_files=None):
    """Create a skill in user_root/skills/<name>/ with optional scripts."""
    skill_dir = os.path.join(get_skills_dir(user_root), name)
    os.makedirs(skill_dir, exist_ok=True)
    content = f"""---
name: {name}
description: {desc}
version: {version}
tags: [test]
---

Skill instructions for {name}.
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(content)

    if with_scripts:
        scripts_dir = os.path.join(skill_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        files = script_files or {"helper.py": "print('hello')"}
        for fname, body in files.items():
            with open(os.path.join(scripts_dir, fname), "w") as f:
                f.write(body)

    return skill_dir


def _create_catalog_env(tmp_workspace, skill_name, version):
    """Create a fake catalog .venv/bin for a skill."""
    env_bin = os.path.join(
        tmp_workspace, "_catalog", "skills", skill_name, version, ".venv", "bin",
    )
    os.makedirs(env_bin, exist_ok=True)
    with open(os.path.join(env_bin, "python"), "w") as f:
        f.write("#!/usr/bin/env python\n")
    return env_bin


# ── Test: Skill load materializes scripts ────────────────────────────────────


class TestSkillLoadMaterializesScripts:

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_copies_scripts_to_session_dir(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "pdf-rename", version="1.1.0", script_files={
            "pdf_extract_text.py": "import pdfplumber\n",
            "pdf_split.py": "import PyPDF2\n",
        })

        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="pdf-rename")

        assert result["is_error"] is False
        assert "[Skill: pdf-rename" in result["output"]

        # Scripts should be materialized
        dst_scripts = os.path.join(ctx.session_dir, "scripts")
        assert os.path.isdir(dst_scripts)
        assert os.path.isfile(os.path.join(dst_scripts, "pdf_extract_text.py"))
        assert os.path.isfile(os.path.join(dst_scripts, "pdf_split.py"))

        # Content preserved
        with open(os.path.join(dst_scripts, "pdf_extract_text.py")) as f:
            assert "pdfplumber" in f.read()

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_no_scripts_dir_no_materialization(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "text-only", with_scripts=False)

        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="text-only")

        assert result["is_error"] is False
        # No scripts dir should be created
        assert not os.path.exists(os.path.join(ctx.session_dir, "scripts"))

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_empty_scripts_dir_no_mapping(self, _mock_disabled, tmp_workspace, user_root, ctx):
        skill_dir = _create_user_skill(user_root, "empty-scripts", with_scripts=False)
        # Create empty scripts dir
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)

        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="empty-scripts")

        assert result["is_error"] is False
        envs = read_skill_envs(ctx.session_dir)
        assert envs["entries"] == {}


# ── Test: Env mapping registration ───────────────────────────────────────────


class TestSkillLoadEnvMapping:

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_registers_env_when_catalog_exists(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "pdf-rename", version="1.1.0", script_files={
            "extract.py": "# extract",
        })
        env_bin = _create_catalog_env(tmp_workspace, "pdf-rename", "1.1.0")

        tool = SkillTool()
        await tool.execute(ctx, action="load", name="pdf-rename")

        envs = read_skill_envs(ctx.session_dir)
        assert "scripts/extract.py" in envs["entries"]
        entry = envs["entries"]["scripts/extract.py"]
        assert entry["skill_name"] == "pdf-rename"
        assert entry["skill_version"] == "1.1.0"
        assert entry["env_bin"] == env_bin

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_no_env_mapping_when_no_catalog_env(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "no-env-skill", version="1.0.0", script_files={
            "run.py": "# run",
        })
        # No catalog env created

        tool = SkillTool()
        await tool.execute(ctx, action="load", name="no-env-skill")

        # Scripts should still be materialized
        assert os.path.isfile(os.path.join(ctx.session_dir, "scripts", "run.py"))
        # But no env mapping
        envs = read_skill_envs(ctx.session_dir)
        assert envs["entries"] == {}

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_multi_skill_accumulates_mappings(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "pdf-rename", version="1.1.0", script_files={
            "split.py": "# split",
        })
        _create_user_skill(user_root, "ocr", version="0.1.0", script_files={
            "scan.py": "# scan",
        })
        env1 = _create_catalog_env(tmp_workspace, "pdf-rename", "1.1.0")
        env2 = _create_catalog_env(tmp_workspace, "ocr", "0.1.0")

        tool = SkillTool()
        await tool.execute(ctx, action="load", name="pdf-rename")
        await tool.execute(ctx, action="load", name="ocr")

        envs = read_skill_envs(ctx.session_dir)
        assert len(envs["entries"]) == 2
        assert envs["entries"]["scripts/split.py"]["skill_name"] == "pdf-rename"
        assert envs["entries"]["scripts/split.py"]["env_bin"] == env1
        assert envs["entries"]["scripts/scan.py"]["skill_name"] == "ocr"
        assert envs["entries"]["scripts/scan.py"]["env_bin"] == env2

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_reload_same_skill_updates_mapping(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "pdf-rename", version="1.1.0", script_files={
            "extract.py": "# v1",
        })
        env_bin = _create_catalog_env(tmp_workspace, "pdf-rename", "1.1.0")

        tool = SkillTool()
        await tool.execute(ctx, action="load", name="pdf-rename")
        await tool.execute(ctx, action="load", name="pdf-rename")

        envs = read_skill_envs(ctx.session_dir)
        # Should still be exactly 1 entry, not duplicated
        assert len(envs["entries"]) == 1
        assert envs["entries"]["scripts/extract.py"]["env_bin"] == env_bin


# ── Test: Materialization is best-effort ─────────────────────────────────────


class TestMaterializationBestEffort:

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_load_succeeds_even_if_materialization_fails(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "fragile", version="1.0.0", script_files={
            "run.py": "# code",
        })

        # Make session_dir/scripts read-only to force copy failure
        scripts_dir = os.path.join(ctx.session_dir, "scripts")
        os.makedirs(scripts_dir)
        os.chmod(scripts_dir, 0o444)

        tool = SkillTool()
        try:
            result = await tool.execute(ctx, action="load", name="fragile")
            # Load should still succeed
            assert result["is_error"] is False
            assert "[Skill: fragile" in result["output"]
        finally:
            os.chmod(scripts_dir, 0o755)


# ── Test: Existing behavior preserved ────────────────────────────────────────


class TestExistingBehaviorPreserved:

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_load_returns_content_and_metadata(self, _mock_disabled, tmp_workspace, user_root, ctx):
        _create_user_skill(user_root, "my-skill", version="2.0.0", desc="My description")

        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="my-skill")

        assert result["is_error"] is False
        assert result["skill_name"] == "my-skill"
        assert result["skill_version"] == "2.0.0"
        assert "[Skill: my-skill v2.0.0]" in result["output"]
        assert "Skill instructions for my-skill" in result["output"]

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_load_nonexistent_returns_error(self, _mock_disabled, tmp_workspace, user_root, ctx):
        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="nonexistent")
        assert result["is_error"] is True
        assert "not found" in result["output"].lower()

    @pytest.mark.asyncio
    @patch.object(SkillTool, "_get_disabled_skills", new_callable=AsyncMock, return_value=set())
    async def test_load_path_traversal_rejected(self, _mock_disabled, tmp_workspace, user_root, ctx):
        tool = SkillTool()
        result = await tool.execute(ctx, action="load", name="../../../etc")
        assert result["is_error"] is True
        assert "Invalid" in result["output"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
