"""Filesystem-based SkillTool (Phase 6.7, §7.5, updated Phase H1 + M4-C).

Skills are discovered from ``user_root/skills/{name}/SKILL.md``.
The DB ``skills`` table is only used for catalog/audit, not runtime content.
The ``user_skills`` table is checked for admin-level disable (Phase H1).

Phase M4-C additions:
  - ``skill load`` materializes bundled scripts to ``session_dir/scripts/``
  - Registers script→env mappings in ``session_dir/.agentd/skill_envs.json``
"""

import logging
import os
import shutil
import uuid
from typing import Any

from tools.base import BaseTool, ToolContext
from skills.filesystem import get_skills_dir
from skills.package import parse_frontmatter, strip_frontmatter
from skills.env import get_catalog_skill_env_bin, register_skill_scripts

logger = logging.getLogger(__name__)

# Backward-compat aliases for any external callers
_parse_frontmatter = parse_frontmatter
_strip_frontmatter = strip_frontmatter


class SkillTool(BaseTool):
    """List installed skills or load a specific skill's content.

    Operates on the filesystem: ``user_root/skills/{name}/SKILL.md``.
    This is read-only with no side effects. The ``load`` action returns
    the full skill content for the LLM to consume.

    Respects user-level disable: if an admin has disabled a skill for
    the current user via ``user_skills.is_enabled=False``, ``list``
    will hide it and ``load`` will reject it.
    """

    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "List installed skills or load a skill by name."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "load"],
                    "description": "'list' to list all installed skills, 'load' to load a specific skill.",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (required when action='load').",
                },
            },
            "required": ["action"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        action: str = kwargs["action"]
        skills_dir = get_skills_dir(ctx.user_root)

        # Load disabled skill names for this user (Phase H1)
        disabled = await self._get_disabled_skills(ctx.user_id)

        if action == "list":
            return self._list_skills(skills_dir, disabled)
        elif action == "load":
            skill_name = kwargs.get("name")
            if not skill_name:
                return {"output": "name is required for action 'load'", "is_error": True}
            if skill_name in disabled:
                return {"output": f"Skill '{skill_name}' is disabled for your account", "is_error": True}
            return self._load_skill(skills_dir, skill_name, ctx)

        return {"output": f"Unknown action: {action}", "is_error": True}

    @staticmethod
    async def _get_disabled_skills(user_id: str) -> set[str]:
        """Return set of skill names disabled for this user."""
        try:
            from core.database import AsyncSessionLocal
            from skills import user_skill_service as us_svc
            uid = uuid.UUID(user_id)
            async with AsyncSessionLocal() as db:
                all_skills = await us_svc.list_user_skills(db, uid)
                return {s.skill_name for s in all_skills if not s.is_enabled}
        except Exception:
            return set()

    def _list_skills(self, skills_dir: str, disabled: set[str]) -> dict[str, Any]:
        """Scan skills directory and return metadata from SKILL.md frontmatter."""
        if not os.path.isdir(skills_dir):
            return {"output": [], "is_error": False}

        skills: list[dict] = []
        try:
            for entry in sorted(os.listdir(skills_dir)):
                skill_path = os.path.join(skills_dir, entry)
                if not os.path.isdir(skill_path):
                    continue
                skill_md = os.path.join(skill_path, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue

                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()
                    meta = _parse_frontmatter(content)
                    name = meta.get("name", entry)
                    if name in disabled:
                        continue
                    skills.append({
                        "name": name,
                        "description": meta.get("description", ""),
                        "tags": meta.get("tags", []),
                    })
                except Exception:
                    # Skip unreadable skills
                    continue
        except OSError:
            pass

        return {"output": skills, "is_error": False}

    def _load_skill(self, skills_dir: str, skill_name: str, ctx: ToolContext) -> dict[str, Any]:
        """Load a specific skill's SKILL.md content.

        Phase M4-C: also materializes bundled scripts to session_dir/scripts/
        and registers script→env mappings in .agentd/skill_envs.json.
        """
        # Prevent path traversal in skill name
        safe_name = os.path.basename(skill_name)
        if not safe_name or safe_name != skill_name:
            return {"output": "Invalid skill name", "is_error": True}

        skill_dir = os.path.join(skills_dir, safe_name)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            return {"output": f"Skill not found: {skill_name}", "is_error": True}

        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return {"output": f"Error reading skill: {e}", "is_error": True}

        meta = _parse_frontmatter(content)
        version = meta.get("version", "0.1.0")
        resolved_name = meta.get("name", skill_name)

        # Phase M4-C: materialize scripts + register env mapping
        self._materialize_skill_scripts(skill_dir, resolved_name, version, ctx)

        return {
            "action": "load",
            "content": content,
            "skill_name": resolved_name,
            "skill_version": version,
            "output": f"[Skill: {skill_name} v{version}]\n\n{content}",
            "is_error": False,
        }

    @staticmethod
    def _materialize_skill_scripts(
        skill_dir: str,
        skill_name: str,
        skill_version: str,
        ctx: ToolContext,
    ) -> None:
        """Copy bundled scripts to session_dir and register env mappings.

        - Copies ``skill_dir/scripts/*.py`` → ``session_dir/scripts/``
        - Resolves catalog env_bin for this skill/version
        - Writes mappings to ``session_dir/.agentd/skill_envs.json``

        Failures are logged but do not block skill load (best-effort).
        """
        src_scripts = os.path.join(skill_dir, "scripts")
        if not os.path.isdir(src_scripts):
            return

        try:
            dst_scripts = os.path.join(ctx.session_dir, "scripts")
            os.makedirs(dst_scripts, exist_ok=True)

            # Collect relative paths of copied scripts
            materialized: list[str] = []
            for entry in os.listdir(src_scripts):
                src_file = os.path.join(src_scripts, entry)
                if not os.path.isfile(src_file):
                    continue
                dst_file = os.path.join(dst_scripts, entry)
                shutil.copy2(src_file, dst_file)
                materialized.append(f"scripts/{entry}")

            if not materialized:
                return

            # Resolve catalog env_bin
            env_bin = get_catalog_skill_env_bin(skill_name, skill_version)

            if env_bin:
                register_skill_scripts(
                    ctx.session_dir,
                    skill_name,
                    skill_version,
                    env_bin,
                    materialized,
                )
                logger.info(
                    "Materialized %d scripts for skill %s v%s, env_bin=%s",
                    len(materialized), skill_name, skill_version, env_bin,
                )
            else:
                logger.info(
                    "Materialized %d scripts for skill %s v%s (no catalog env)",
                    len(materialized), skill_name, skill_version,
                )
        except Exception:
            logger.warning(
                "Failed to materialize scripts for skill %s", skill_name,
                exc_info=True,
            )
