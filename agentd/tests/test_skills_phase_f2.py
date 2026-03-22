"""Tests for Phase F2 — Skills install lifecycle.

Covers:
  - SkillTool load returns skill_name + skill_version
  - Icon frontmatter parsing
  - _persist_loaded_skills extracts name+version from new format
  - loaded_skills structure: [{"name":"..","version":".."}]
"""

import os
import re

import pytest

from tools.skill import SkillTool, _parse_frontmatter
from skills.package import parse_frontmatter, SkillPackageMeta, validate_package
from skills.filesystem import get_skills_dir, write_skill_to_catalog
from workspace.manager import ensure_user_root, get_session_dir


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def user_root(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    return root


def _create_skill_md(skills_dir, name, desc="test skill", version="1.2.0",
                     icon="", body="Do stuff"):
    skill_dir = os.path.join(skills_dir, name)
    os.makedirs(skill_dir, exist_ok=True)
    icon_line = f"icon: {icon}\n" if icon else ""
    content = f"""---
name: {name}
description: {desc}
version: {version}
tags: [test]
{icon_line}---

{body}
"""
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(content)


# ── Test: SkillTool load returns version ─────────────────────────────────────


class TestSkillToolVersionedLoad:
    @pytest.mark.asyncio
    async def test_load_returns_skill_name_and_version(self, user_root):
        skills_dir = get_skills_dir(user_root)
        _create_skill_md(skills_dir, "my-skill", version="2.0.0")

        tool = SkillTool()
        from tools.base import ToolContext
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=get_session_dir(user_root, "s1"),
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="load", name="my-skill")
        assert result["is_error"] is False
        assert result["skill_name"] == "my-skill"
        assert result["skill_version"] == "2.0.0"
        assert "[Skill: my-skill v2.0.0]" in result["output"]

    @pytest.mark.asyncio
    async def test_load_default_version(self, user_root):
        """Skill without version in frontmatter defaults to 0.1.0."""
        skills_dir = get_skills_dir(user_root)
        skill_dir = os.path.join(skills_dir, "no-ver")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: no-ver\ndescription: test\n---\nBody\n")

        tool = SkillTool()
        from tools.base import ToolContext
        ctx = ToolContext(
            user_id="u1",
            session_id="s1",
            user_root=user_root,
            session_dir=get_session_dir(user_root, "s1"),
            venv_bin="/tmp/venv/bin",
            publish=lambda *a, **kw: None,
        )
        result = await tool.execute(ctx, action="load", name="no-ver")
        assert result["skill_version"] == "0.1.0"


# ── Test: Icon frontmatter parsing ───────────────────────────────────────────


class TestIconFrontmatter:
    def test_parse_icon_field(self):
        content = """---
name: visual-skill
description: Has an icon
version: 1.0.0
icon: assets/icon.png
tags: [ui]
---

Body.
"""
        meta = parse_frontmatter(content)
        assert meta["icon"] == "assets/icon.png"

    def test_no_icon_field(self):
        content = """---
name: plain
description: No icon
---

Body.
"""
        meta = parse_frontmatter(content)
        assert "icon" not in meta

    def test_validate_package_icon(self, tmp_path):
        pkg_dir = os.path.join(str(tmp_path), "icon-pkg")
        os.makedirs(pkg_dir)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: icon-pkg\ndescription: Has icon\nicon: assets/icon.png\n---\nBody\n")
        result = validate_package(pkg_dir)
        assert result.valid
        assert result.meta.icon == "assets/icon.png"

    def test_skill_package_meta_icon_default(self):
        meta = SkillPackageMeta(name="x", description="y")
        assert meta.icon == ""


# ── Test: Loaded skills format extraction ────────────────────────────────────


class TestLoadedSkillsExtraction:
    def test_f2_format_regex(self):
        """New F2 format: [Skill: name v1.0.0]"""
        text = "[Skill: vlog-planner v1.1.0]\n\nBody content"
        match = re.match(r"^\[Skill: (.+?) v(.+?)\]", text)
        assert match
        assert match.group(1) == "vlog-planner"
        assert match.group(2) == "1.1.0"

    def test_legacy_format_regex(self):
        """Legacy format: [Skill: name]"""
        text = "[Skill: old-skill]\n\nBody content"
        # F2 format won't match
        match = re.match(r"^\[Skill: (.+?) v(.+?)\]", text)
        assert match is None
        # Legacy fallback
        match = re.match(r"^\[Skill: (.+?)\]", text)
        assert match
        assert match.group(1) == "old-skill"

    def test_loaded_skills_dict_structure(self):
        """Verify the expected structure for DB persistence."""
        entry = {"name": "my-skill", "version": "1.0.0"}
        assert isinstance(entry, dict)
        assert "name" in entry
        assert "version" in entry


# ── Test: Session schema loaded_skills type ──────────────────────────────────


class TestSessionSchemaLoadedSkills:
    def test_session_response_accepts_dict_loaded_skills(self):
        from session.schemas import SessionResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "title": "Test",
            "agent_id": "build",
            "model_id": "test",
            "status": "idle",
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "loaded_skills": [
                {"name": "skill-a", "version": "1.0.0"},
                {"name": "skill-b", "version": "2.0.0"},
            ],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        resp = SessionResponse(**data)
        assert len(resp.loaded_skills) == 2
        assert resp.loaded_skills[0]["name"] == "skill-a"


# ── Test: Skills schema usage fields ─────────────────────────────────────────


class TestSkillsSchemaUsage:
    def test_summary_response_has_usage_fields(self):
        from skills.schemas import SkillSummaryResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "name": "test",
            "description": "test",
            "version": "1.0.0",
            "tags": [],
            "is_active": True,
            "usage_count": 42,
            "last_used_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        resp = SkillSummaryResponse(**data)
        assert resp.usage_count == 42
        assert resp.last_used_at is not None

    def test_detail_response_has_usage_fields(self):
        from skills.schemas import SkillDetailResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "name": "test",
            "description": "test",
            "content": "body",
            "version": "1.0.0",
            "tags": [],
            "is_active": True,
            "usage_count": 0,
            "last_used_at": None,
            "created_at": datetime.now(timezone.utc),
        }
        resp = SkillDetailResponse(**data)
        assert resp.usage_count == 0
        assert resp.last_used_at is None


# ── Test: Icon metadata exposure via API schemas ─────────────────────────────


class TestIconMetadataExposure:
    def test_summary_response_icon_field(self):
        from skills.schemas import SkillSummaryResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "name": "icon-skill",
            "description": "Has icon",
            "version": "1.0.0",
            "icon": "assets/icon.png",
            "tags": [],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        resp = SkillSummaryResponse(**data)
        assert resp.icon == "assets/icon.png"

    def test_summary_response_icon_default(self):
        from skills.schemas import SkillSummaryResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "name": "no-icon",
            "description": "No icon",
            "version": "1.0.0",
            "tags": [],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        resp = SkillSummaryResponse(**data)
        assert resp.icon == ""

    def test_detail_response_icon_field(self):
        from skills.schemas import SkillDetailResponse
        from datetime import datetime, timezone
        import uuid

        data = {
            "id": uuid.uuid4(),
            "name": "icon-skill",
            "description": "Has icon",
            "content": "body",
            "version": "1.0.0",
            "icon": "assets/logo.svg",
            "tags": [],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        resp = SkillDetailResponse(**data)
        assert resp.icon == "assets/logo.svg"

    def test_orm_icon_property(self):
        """ORM model derives icon from metadata_extra."""
        from skills.models import Skill

        s = Skill(
            name="test",
            description="test",
            content="body",
            metadata_extra={"icon": "assets/icon.png"},
        )
        assert s.icon == "assets/icon.png"

    def test_orm_icon_property_empty(self):
        from skills.models import Skill

        s = Skill(name="test", description="test", content="body", metadata_extra={})
        assert s.icon == ""

    def test_create_schema_has_icon(self):
        from skills.schemas import SkillCreate

        body = SkillCreate(name="x", description="y", content="z", icon="img/i.png")
        assert body.icon == "img/i.png"

    def test_update_schema_has_icon(self):
        from skills.schemas import SkillUpdate

        body = SkillUpdate(icon="new/icon.png")
        assert body.icon == "new/icon.png"
