"""Skill Square aggregation service (Phase H3).

Aggregates catalog skills by name, resolves user install state,
provides detail with readme and package tree.
"""

import os
import uuid
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from skills.models import Skill, UserSkill
from skills.filesystem import get_catalog_dir, read_catalog_skill_md
from skills.package import strip_frontmatter


def _has_catalog_version(skill_name: str, version: str) -> bool:
    """Check if a specific skill version has a catalog directory on disk."""
    catalog_dir = get_catalog_dir()
    version_dir = os.path.join(catalog_dir, skill_name, version)
    return os.path.isdir(version_dir)


async def list_square_cards(
    db: AsyncSession,
    user_id: uuid.UUID,
    q: Optional[str] = None,
) -> list[dict]:
    """Return skill cards aggregated by name with user install state.

    Each card represents a unique skill name, aggregating all active versions.
    Ordered by total usage DESC, name ASC.
    """
    # Fetch all active skills
    query = select(Skill).where(Skill.is_active == True)  # noqa: E712
    if q:
        q_lower = f"%{q.lower()}%"
        query = query.where(
            Skill.name.ilike(q_lower)
            | Skill.description.ilike(q_lower)
            | Skill.tags.any(q.lower())
        )
    query = query.order_by(Skill.name, Skill.version.desc())
    rows = (await db.execute(query)).scalars().all()

    if not rows:
        return []

    # Group by name
    grouped: dict[str, list[Skill]] = {}
    for skill in rows:
        grouped.setdefault(skill.name, []).append(skill)

    # Fetch user install state
    user_skills = await _get_user_skills_map(db, user_id)

    # Build cards — only include skills with valid catalog directories
    cards: list[dict] = []
    for name, versions in grouped.items():
        # Filter to versions that actually exist on disk
        valid_versions = [v for v in versions if _has_catalog_version(name, v.version)]
        if not valid_versions:
            continue  # skip phantom skills with no catalog data

        latest = valid_versions[0]  # already sorted desc
        total_usage = sum(v.usage_count for v in valid_versions)
        us = user_skills.get(name)

        cards.append({
            "name": name,
            "description": latest.description,
            "icon": latest.icon,
            "tags": latest.tags or [],
            "latest_version": latest.version,
            "available_versions": [v.version for v in valid_versions],
            "usage_count_total": total_usage,
            "installed": us is not None,
            "installed_version": us.version if us else None,
            "enabled": us.is_enabled if us else None,
        })

    # Sort by usage DESC, name ASC
    cards.sort(key=lambda c: (-c["usage_count_total"], c["name"]))
    return cards


async def get_square_detail(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
    version: Optional[str] = None,
) -> Optional[dict]:
    """Return full detail for a skill, resolved to a specific version.

    If version is not specified:
    - Use the user's installed version if installed
    - Otherwise use the latest version
    """
    # Fetch all active versions for this skill
    query = (
        select(Skill)
        .where(and_(Skill.name == skill_name, Skill.is_active == True))  # noqa: E712
        .order_by(Skill.version.desc())
    )
    rows = (await db.execute(query)).scalars().all()
    if not rows:
        return None

    # Filter to versions with valid catalog directories
    rows = [r for r in rows if _has_catalog_version(skill_name, r.version)]
    if not rows:
        return None

    # User install state
    us = await _get_user_skill(db, user_id, skill_name)

    # Resolve selected version
    if version:
        selected = next((s for s in rows if s.version == version), None)
    elif us:
        selected = next((s for s in rows if s.version == us.version), None)
    else:
        selected = None

    if not selected:
        selected = rows[0]  # latest

    total_usage = sum(v.usage_count for v in rows)

    # Read readme content from catalog
    readme_content = ""
    raw_md = read_catalog_skill_md(skill_name, selected.version)
    if raw_md:
        readme_content = strip_frontmatter(raw_md)

    # Build package tree
    tree = _build_package_tree(skill_name, selected.version)

    return {
        "name": skill_name,
        "description": selected.description,
        "icon": selected.icon,
        "tags": selected.tags or [],
        "selected_version": selected.version,
        "versions": [
            {
                "version": v.version,
                "skill_id": v.id,
                "created_at": v.created_at,
            }
            for v in rows
        ],
        "installed": us is not None,
        "installed_version": us.version if us else None,
        "enabled": us.is_enabled if us else None,
        "selected_skill_id": selected.id,
        "readme_content": readme_content,
        "tree": tree,
        "usage_count_total": total_usage,
    }


# ── Private helpers ──────────────────────────────────────────────────────────


async def _get_user_skills_map(
    db: AsyncSession, user_id: uuid.UUID,
) -> dict[str, UserSkill]:
    """Return {skill_name: UserSkill} for a user."""
    result = await db.execute(
        select(UserSkill).where(UserSkill.user_id == user_id)
    )
    return {us.skill_name: us for us in result.scalars().all()}


async def _get_user_skill(
    db: AsyncSession, user_id: uuid.UUID, skill_name: str,
) -> Optional[UserSkill]:
    """Get a single user_skill record."""
    result = await db.execute(
        select(UserSkill).where(
            and_(UserSkill.user_id == user_id, UserSkill.skill_name == skill_name)
        )
    )
    return result.scalar_one_or_none()


_ALLOWED_TREE_DIRS = {"references", "assets", "scripts"}


def _build_package_tree(skill_name: str, version: str) -> list[dict]:
    """Build a minimal tree of the catalog package for preview.

    Only exposes: SKILL.md, references/, assets/, scripts/.
    """
    catalog_dir = get_catalog_dir()
    version_dir = os.path.join(catalog_dir, skill_name, version)
    if not os.path.isdir(version_dir):
        return []

    nodes: list[dict] = []

    # SKILL.md
    skill_md = os.path.join(version_dir, "SKILL.md")
    if os.path.isfile(skill_md):
        nodes.append({"name": "SKILL.md", "path": "SKILL.md", "type": "file"})

    # Allowed subdirectories
    for dirname in sorted(_ALLOWED_TREE_DIRS):
        subdir = os.path.join(version_dir, dirname)
        if not os.path.isdir(subdir):
            continue
        children = _scan_dir(subdir, dirname)
        nodes.append({
            "name": dirname,
            "path": dirname,
            "type": "dir",
            "children": children,
        })

    return nodes


def _scan_dir(abs_dir: str, rel_prefix: str) -> list[dict]:
    """Recursively scan a directory for tree preview."""
    children: list[dict] = []
    try:
        for entry in sorted(os.listdir(abs_dir)):
            if entry.startswith("."):
                continue
            abs_path = os.path.join(abs_dir, entry)
            rel_path = f"{rel_prefix}/{entry}"
            if os.path.isdir(abs_path):
                sub = _scan_dir(abs_path, rel_path)
                children.append({
                    "name": entry,
                    "path": rel_path,
                    "type": "dir",
                    "children": sub,
                })
            elif os.path.isfile(abs_path):
                children.append({
                    "name": entry,
                    "path": rel_path,
                    "type": "file",
                })
    except OSError:
        pass
    return children
