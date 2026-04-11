"""Tests for workspace management, filesystem SkillTool, and session_dir lifecycle.

Covers audit issue #32.
"""

import os
import shutil
import tempfile
import uuid

import pytest

from workspace.manager import (
    ensure_user_root,
    get_session_dir,
    get_skills_dir,
    validate_path,
    write_skill_to_catalog,
    remove_skill_from_catalog,
    install_skill_for_user,
    uninstall_skill_for_user,
)
from tools.skill import SkillTool, _parse_frontmatter, _strip_frontmatter
from workspace.router import _get_preview_info


# ── Fixture: temporary workspace root ──────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace root."""
    return str(tmp_path)


@pytest.fixture
def user_root(tmp_workspace):
    """Create a user_root with standard subdirectories."""
    root = os.path.join(tmp_workspace, "test-user")
    ensure_user_root(root)
    return root


# ── Test: ensure_user_root (login self-heal) ───────────────────────────────


class TestEnsureUserRoot:
    def test_creates_all_subdirs(self, tmp_workspace):
        root = os.path.join(tmp_workspace, "new-user")
        assert not os.path.exists(root)

        ensure_user_root(root)

        assert os.path.isdir(root)
        assert os.path.isdir(os.path.join(root, "sessions"))
        assert os.path.isdir(os.path.join(root, "skills"))

    def test_idempotent(self, user_root):
        """Calling twice should not raise."""
        ensure_user_root(user_root)
        assert os.path.isdir(os.path.join(user_root, "sessions"))

    def test_recovers_deleted_subdirs(self, user_root):
        """If sessions/ is deleted, self-heal recreates it."""
        shutil.rmtree(os.path.join(user_root, "sessions"))
        assert not os.path.exists(os.path.join(user_root, "sessions"))

        ensure_user_root(user_root)
        assert os.path.isdir(os.path.join(user_root, "sessions"))


# ── Test: get_session_dir ──────────────────────────────────────────────────


class TestGetSessionDir:
    def test_creates_session_dir(self, user_root):
        sid = str(uuid.uuid4())
        session_dir = get_session_dir(user_root, sid)

        assert os.path.isdir(session_dir)
        assert session_dir == os.path.join(user_root, "sessions", sid)

    def test_idempotent(self, user_root):
        sid = str(uuid.uuid4())
        d1 = get_session_dir(user_root, sid)
        d2 = get_session_dir(user_root, sid)
        assert d1 == d2
        assert os.path.isdir(d1)


# ── Test: validate_path ────────────────────────────────────────────────────


class TestValidatePath:
    def test_valid_relative_path(self, user_root):
        session_dir = get_session_dir(user_root, "s1")
        # Create a file inside
        test_file = os.path.join(session_dir, "hello.txt")
        with open(test_file, "w") as f:
            f.write("hi")

        result = validate_path(session_dir, "hello.txt")
        assert result == os.path.realpath(test_file)

    def test_rejects_path_traversal(self, user_root):
        session_dir = get_session_dir(user_root, "s1")
        with pytest.raises(PermissionError, match="Path escape"):
            validate_path(session_dir, "../../etc/passwd")

    def test_rejects_absolute_path_outside(self, user_root):
        session_dir = get_session_dir(user_root, "s1")
        with pytest.raises(PermissionError, match="Path escape"):
            validate_path(session_dir, "/etc/passwd")


# ── Test: SkillTool (filesystem-based) ─────────────────────────────────────


class TestSkillToolFilesystem:
    def _create_skill_md(self, skills_dir, name, desc="test skill", content="Do stuff"):
        skill_dir = os.path.join(skills_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        skill_md = f"""---
name: {name}
description: {desc}
tags: [test, demo]
---

{content}
"""
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(skill_md)

    @pytest.mark.asyncio
    async def test_list_skills_empty(self, user_root):
        tool = SkillTool()
        from tools.base import ToolContext
        session_dir = get_session_dir(user_root, "s1")
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="list")
        assert result["is_error"] is False
        assert result["output"] == []

    @pytest.mark.asyncio
    async def test_list_skills_finds_installed(self, user_root):
        skills_dir = get_skills_dir(user_root)
        self._create_skill_md(skills_dir, "code_review", "Review code quality")
        self._create_skill_md(skills_dir, "testing", "Write tests")

        tool = SkillTool()
        from tools.base import ToolContext
        session_dir = get_session_dir(user_root, "s1")
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="list")
        assert result["is_error"] is False
        names = [s["name"] for s in result["output"]]
        assert "code_review" in names
        assert "testing" in names

    @pytest.mark.asyncio
    async def test_load_skill_returns_content(self, user_root):
        skills_dir = get_skills_dir(user_root)
        self._create_skill_md(skills_dir, "my_skill", content="Custom instructions here")

        tool = SkillTool()
        from tools.base import ToolContext
        session_dir = get_session_dir(user_root, "s1")
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="load", name="my_skill")
        assert result["is_error"] is False
        assert "[Skill: my_skill" in result["output"]
        assert "Custom instructions here" in result["output"]
        assert result.get("skill_name") == "my_skill"
        assert result.get("skill_version") == "0.1.0"

    @pytest.mark.asyncio
    async def test_load_skill_path_traversal(self, user_root):
        tool = SkillTool()
        from tools.base import ToolContext
        session_dir = get_session_dir(user_root, "s1")
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="load", name="../../../etc")
        assert result["is_error"] is True
        assert "Invalid" in result["output"]

    @pytest.mark.asyncio
    async def test_load_nonexistent_skill(self, user_root):
        tool = SkillTool()
        from tools.base import ToolContext
        session_dir = get_session_dir(user_root, "s1")
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=session_dir,
            workspace_dir=session_dir,
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="load", name="nonexistent")
        assert result["is_error"] is True
        assert "not found" in result["output"].lower()


# ── Test: _parse_frontmatter / _strip_frontmatter ──────────────────────────


class TestFrontmatterParsing:
    def test_parse_frontmatter(self):
        content = """---
name: test_skill
description: A test skill
tags: [python, testing]
---

Body content here.
"""
        meta = _parse_frontmatter(content)
        assert meta["name"] == "test_skill"
        assert meta["description"] == "A test skill"
        assert meta["tags"] == ["python", "testing"]

    def test_strip_frontmatter(self):
        content = """---
name: test
---

Body here.
"""
        body = _strip_frontmatter(content)
        assert body.strip() == "Body here."

    def test_parse_no_frontmatter(self):
        assert _parse_frontmatter("just plain text") == {}


# ── Test: catalog sync + install/uninstall ─────────────────────────────────


class TestCatalogInstall:
    def test_write_and_remove_catalog(self, tmp_path, monkeypatch):
        """write_skill_to_catalog creates SKILL.md, remove cleans it up."""
        from core import config
        monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))

        path = write_skill_to_catalog("review", "Code review", "Review code", ["python"])
        assert os.path.isfile(path)
        with open(path) as f:
            content = f.read()
        assert "name: review" in content
        assert "Review code" in content

        remove_skill_from_catalog("review")
        assert not os.path.exists(os.path.dirname(path))

    def test_install_and_uninstall(self, tmp_path, monkeypatch):
        """install copies from catalog to user skills, uninstall removes."""
        from core import config
        monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))

        user_root = os.path.join(str(tmp_path), "user1")
        ensure_user_root(user_root)

        # Create catalog skill
        write_skill_to_catalog("my_skill", "desc", "content", [])

        # Install
        install_skill_for_user(user_root, "my_skill")
        user_skill_md = os.path.join(user_root, "skills", "my_skill", "SKILL.md")
        assert os.path.isfile(user_skill_md)

        # Uninstall
        assert uninstall_skill_for_user(user_root, "my_skill") is True
        assert not os.path.exists(os.path.join(user_root, "skills", "my_skill"))

    def test_install_nonexistent_raises(self, tmp_path, monkeypatch):
        from core import config
        monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))

        user_root = os.path.join(str(tmp_path), "user1")
        ensure_user_root(user_root)

        with pytest.raises(FileNotFoundError):
            install_skill_for_user(user_root, "nonexistent")

    def test_uninstall_not_installed(self, user_root):
        assert uninstall_skill_for_user(user_root, "not_here") is False


# ── Test: file preview mode detection ──────────────────────────────────────


class TestPreviewInfo:
    def test_text_extensions(self):
        assert _get_preview_info(".py") == (True, "text", False)
        assert _get_preview_info(".md") == (True, "text", False)
        assert _get_preview_info(".json") == (True, "text", False)

    def test_image_extensions(self):
        assert _get_preview_info(".png") == (True, "image", False)
        assert _get_preview_info(".jpg") == (True, "image", False)

    def test_pdf(self):
        assert _get_preview_info(".pdf") == (True, "pdf", False)

    def test_office_extensions(self):
        assert _get_preview_info(".docx") == (True, "office", False)
        assert _get_preview_info(".xlsx") == (True, "office", False)
        assert _get_preview_info(".pptx") == (True, "office", False)
        assert _get_preview_info(".doc") == (True, "office", False)
        assert _get_preview_info(".xls") == (True, "office", False)

    def test_unknown_extension(self):
        assert _get_preview_info(".bin") == (False, "download", True)
        assert _get_preview_info(".dat") == (False, "download", True)
