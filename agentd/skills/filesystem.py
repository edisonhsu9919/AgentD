"""Skill catalog filesystem operations (Phase F1).

Replaces the catalog helpers previously in workspace/manager.py.
Catalog directory structure (versioned):

    _catalog/skills/<skill-name>/<version>/SKILL.md
    _catalog/skills/<skill-name>/<version>/references/
    _catalog/skills/<skill-name>/<version>/assets/
    _catalog/skills/<skill-name>/<version>/scripts/

User runtime directory (unchanged):

    user_root/skills/<skill-name>/SKILL.md
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from core.config import settings
from skills.package import SkillPackageMeta, parse_frontmatter, strip_frontmatter


# ---------------------------------------------------------------------------
# Catalog directory helpers
# ---------------------------------------------------------------------------

def get_catalog_dir() -> str:
    """Return the global skills catalog root, creating if needed."""
    catalog_dir = os.path.join(settings.workspace_root, "_catalog", "skills")
    os.makedirs(catalog_dir, exist_ok=True)
    return catalog_dir


def get_skills_dir(user_root: str) -> str:
    """Return the user's installed skills directory."""
    return os.path.join(user_root, "skills")


# ---------------------------------------------------------------------------
# Catalog write / remove (versioned)
# ---------------------------------------------------------------------------

def write_skill_to_catalog(
    meta: SkillPackageMeta,
    content: str,
) -> str:
    """Write a skill to the versioned catalog directory.

    Directory: ``_catalog/skills/<name>/<version>/SKILL.md``
    Returns the absolute path of the written SKILL.md.
    """
    catalog_dir = get_catalog_dir()
    version_dir = os.path.join(catalog_dir, meta.name, meta.version)
    os.makedirs(version_dir, exist_ok=True)

    skill_md = _build_skill_md(meta, content)
    skill_path = os.path.join(version_dir, "SKILL.md")
    with open(skill_path, "w", encoding="utf-8") as f:
        f.write(skill_md)
    return skill_path


def remove_skill_from_catalog(name: str, version: str | None = None) -> None:
    """Remove a skill (or a specific version) from the catalog.

    If *version* is None, removes the entire skill directory (all versions).
    """
    catalog_dir = get_catalog_dir()
    if version:
        target = os.path.join(catalog_dir, name, version)
    else:
        target = os.path.join(catalog_dir, name)
    if os.path.isdir(target):
        shutil.rmtree(target)
        # Clean up empty parent if we removed just a version
        if version:
            parent = os.path.join(catalog_dir, name)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)


def import_package_to_catalog(package_dir: str, meta: SkillPackageMeta) -> str:
    """Import a local skill package directory into the versioned catalog.

    Copies SKILL.md and optional resource dirs (references/, assets/, scripts/).
    Returns the catalog version directory path.
    """
    catalog_dir = get_catalog_dir()
    version_dir = os.path.join(catalog_dir, meta.name, meta.version)

    # Remove existing version if present
    if os.path.isdir(version_dir):
        shutil.rmtree(version_dir)
    os.makedirs(version_dir, exist_ok=True)

    # Copy SKILL.md
    src_md = os.path.join(package_dir, "SKILL.md")
    shutil.copy2(src_md, os.path.join(version_dir, "SKILL.md"))

    # Copy optional resource directories
    for subdir in ("references", "assets", "scripts"):
        src_sub = os.path.join(package_dir, subdir)
        if os.path.isdir(src_sub):
            shutil.copytree(src_sub, os.path.join(version_dir, subdir))

    return version_dir


# ---------------------------------------------------------------------------
# Catalog query helpers
# ---------------------------------------------------------------------------

def list_catalog_versions(skill_name: str) -> list[str]:
    """Return sorted list of version strings for a skill in the catalog."""
    catalog_dir = get_catalog_dir()
    skill_dir = os.path.join(catalog_dir, skill_name)
    if not os.path.isdir(skill_dir):
        return []
    versions = [
        entry for entry in sorted(os.listdir(skill_dir))
        if os.path.isdir(os.path.join(skill_dir, entry))
    ]
    return versions


def get_latest_version(skill_name: str) -> str | None:
    """Return the latest (last sorted) version string, or None."""
    versions = list_catalog_versions(skill_name)
    return versions[-1] if versions else None


def read_catalog_skill_md(skill_name: str, version: str) -> str | None:
    """Read the SKILL.md content for a specific catalog version."""
    catalog_dir = get_catalog_dir()
    path = os.path.join(catalog_dir, skill_name, version, "SKILL.md")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# User install / uninstall (version-aware)
# ---------------------------------------------------------------------------

def install_skill_for_user(
    user_root: str,
    skill_name: str,
    version: str | None = None,
) -> bool:
    """Copy a skill from catalog to the user's skills directory.

    If *version* is None, installs the latest version.
    Returns True on success. Raises FileNotFoundError if not in catalog.
    """
    catalog_dir = get_catalog_dir()

    if version is None:
        version = get_latest_version(skill_name)
    if not version:
        raise FileNotFoundError(f"Skill '{skill_name}' not found in catalog")

    catalog_version_dir = os.path.join(catalog_dir, skill_name, version)
    if not os.path.isdir(catalog_version_dir):
        raise FileNotFoundError(
            f"Skill '{skill_name}' version '{version}' not found in catalog"
        )

    user_skill = os.path.join(get_skills_dir(user_root), skill_name)
    if os.path.isdir(user_skill):
        shutil.rmtree(user_skill)

    shutil.copytree(catalog_version_dir, user_skill)
    return True


def uninstall_skill_for_user(user_root: str, skill_name: str) -> bool:
    """Remove a skill from the user's installed skills directory."""
    safe_name = os.path.basename(skill_name)
    if not safe_name or safe_name != skill_name:
        return False
    user_skill = os.path.join(get_skills_dir(user_root), safe_name)
    if os.path.isdir(user_skill):
        shutil.rmtree(user_skill)
        return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_skill_md(meta: SkillPackageMeta, body: str) -> str:
    """Build a SKILL.md string from meta + body content."""
    tags_str = "[" + ", ".join(meta.tags) + "]" if meta.tags else "[]"

    lines = [
        "---",
        f"name: {meta.name}",
        f"description: {meta.description}",
        f"version: {meta.version}",
    ]
    if meta.license:
        lines.append(f"license: {meta.license}")
    if meta.compatibility:
        lines.append(f"compatibility: {meta.compatibility}")
    lines.append(f"tags: {tags_str}")
    if meta.metadata:
        lines.append("metadata:")
        for k, v in meta.metadata.items():
            lines.append(f"  {k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)

    return "\n".join(lines)
