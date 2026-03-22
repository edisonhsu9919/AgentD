"""Skill package parser — reads and validates SKILL.md packages (Phase F1).

Parses YAML frontmatter from SKILL.md files with support for:
  - Required: name, description
  - Optional: version, license, compatibility, metadata, tags

Also detects optional resource sub-directories (references/, assets/, scripts/).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillPackageMeta:
    """Parsed metadata from a SKILL.md frontmatter block."""

    name: str
    description: str
    version: str = "0.1.0"
    license: str = ""
    compatibility: str = ""
    icon: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    # Body text (everything after the frontmatter)
    body: str = ""
    # Optional resource directories detected in the package
    has_references: bool = False
    has_assets: bool = False
    has_scripts: bool = False


@dataclass
class SkillPackageValidationResult:
    """Result of validating a skill package directory."""

    valid: bool
    meta: SkillPackageMeta | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frontmatter parser (enhanced from tools/skill.py _parse_frontmatter)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _strip_yaml_quotes(value: str) -> str:
    """Strip surrounding YAML quotes (single or double) from a string value."""
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"):
            return value[1:-1]
    return value


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md file.

    Supports: name, description, version, license, compatibility,
    metadata (nested key:value), tags (inline YAML list).
    Unknown fields are silently ignored.
    """
    meta: dict[str, Any] = {}
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return meta

    metadata_block: dict[str, Any] = {}
    in_metadata = False

    for line in match.group(1).split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect indented lines under 'metadata:'
        if in_metadata:
            if line.startswith("  ") and ":" in stripped:
                k, _, v = stripped.partition(":")
                metadata_block[k.strip()] = _strip_yaml_quotes(v.strip())
                continue
            else:
                in_metadata = False

        if stripped.startswith("name:"):
            meta["name"] = _strip_yaml_quotes(stripped[5:].strip())
        elif stripped.startswith("description:"):
            meta["description"] = _strip_yaml_quotes(stripped[12:].strip())
        elif stripped.startswith("version:"):
            meta["version"] = _strip_yaml_quotes(stripped[8:].strip())
        elif stripped.startswith("license:"):
            meta["license"] = _strip_yaml_quotes(stripped[8:].strip())
        elif stripped.startswith("compatibility:"):
            meta["compatibility"] = _strip_yaml_quotes(stripped[14:].strip())
        elif stripped.startswith("icon:"):
            meta["icon"] = _strip_yaml_quotes(stripped[5:].strip())
        elif stripped.startswith("tags:"):
            tags_str = stripped[5:].strip()
            if tags_str.startswith("[") and tags_str.endswith("]"):
                meta["tags"] = [_strip_yaml_quotes(t.strip()) for t in tags_str[1:-1].split(",") if t.strip()]
            else:
                meta["tags"] = []
        elif stripped == "metadata:":
            in_metadata = True
        elif stripped.startswith("metadata:"):
            # Single-line metadata (unlikely but handle)
            val = stripped[9:].strip()
            if val:
                metadata_block["_raw"] = val

    if metadata_block:
        meta["metadata"] = metadata_block

    return meta


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter, returning just the body."""
    return _FRONTMATTER_RE.sub("", content, count=1)


# ---------------------------------------------------------------------------
# Package validation
# ---------------------------------------------------------------------------

_RESOURCE_DIRS = ("references", "assets", "scripts")


def validate_package(package_dir: str) -> SkillPackageValidationResult:
    """Validate a skill package directory and extract metadata.

    A valid package must contain a SKILL.md with at least name and description
    in the frontmatter.
    """
    errors: list[str] = []

    if not os.path.isdir(package_dir):
        return SkillPackageValidationResult(valid=False, errors=[f"Not a directory: {package_dir}"])

    skill_md_path = os.path.join(package_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return SkillPackageValidationResult(valid=False, errors=["Missing SKILL.md"])

    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return SkillPackageValidationResult(valid=False, errors=[f"Cannot read SKILL.md: {e}"])

    fm = parse_frontmatter(content)

    if not fm.get("name"):
        errors.append("Frontmatter missing required field: name")
    if not fm.get("description"):
        errors.append("Frontmatter missing required field: description")

    if errors:
        return SkillPackageValidationResult(valid=False, errors=errors)

    body = strip_frontmatter(content)

    meta = SkillPackageMeta(
        name=fm["name"],
        description=fm["description"],
        version=fm.get("version", "0.1.0"),
        license=fm.get("license", ""),
        compatibility=fm.get("compatibility", ""),
        icon=fm.get("icon", ""),
        metadata=fm.get("metadata", {}),
        tags=fm.get("tags", []),
        body=body,
        has_references=os.path.isdir(os.path.join(package_dir, "references")),
        has_assets=os.path.isdir(os.path.join(package_dir, "assets")),
        has_scripts=os.path.isdir(os.path.join(package_dir, "scripts")),
    )

    return SkillPackageValidationResult(valid=True, meta=meta)
