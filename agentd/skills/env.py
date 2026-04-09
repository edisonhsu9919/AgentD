"""Skill runtime environment helpers (Phase M4-A).

Manages the session-level skill env mapping at:

    session_dir/.agentd/skill_envs.json

This file maps materialized skill scripts (relative paths within session_dir)
to their corresponding catalog venv bin paths, enabling per-call env resolution
in bash/script tools without modifying ToolContext.

Schema (v1):

    {
      "version": 1,
      "entries": {
        "scripts/pdf_extract_text.py": {
          "skill_name": "pdf-rename",
          "skill_version": "1.1.0",
          "env_bin": "/.../_catalog/skills/pdf-rename/1.1.0/.venv/bin"
        }
      }
    }
"""

from __future__ import annotations

import json
import os
from typing import Any

from core.config import settings


_ENVS_DIR = ".agentd"
_ENVS_FILE = "skill_envs.json"
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------


def _envs_path(session_dir: str) -> str:
    return os.path.join(session_dir, _ENVS_DIR, _ENVS_FILE)


def read_skill_envs(session_dir: str) -> dict[str, Any]:
    """Read the skill envs mapping for a session.

    Returns the full mapping dict, or an empty ``{"version": 1, "entries": {}}``
    if the file does not exist or is malformed.
    """
    path = _envs_path(session_dir)
    if not os.path.isfile(path):
        return {"version": _SCHEMA_VERSION, "entries": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            return {"version": _SCHEMA_VERSION, "entries": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": _SCHEMA_VERSION, "entries": {}}


def _write_skill_envs(session_dir: str, data: dict[str, Any]) -> None:
    """Write the skill envs mapping atomically (write-tmp + rename)."""
    dir_path = os.path.join(session_dir, _ENVS_DIR)
    os.makedirs(dir_path, exist_ok=True)
    path = _envs_path(session_dir)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def register_skill_scripts(
    session_dir: str,
    skill_name: str,
    skill_version: str,
    env_bin: str,
    script_rel_paths: list[str],
) -> None:
    """Register materialized skill scripts in the session env mapping.

    Adds entries for each script_rel_path (relative to session_dir) pointing
    to the given skill's catalog env_bin. Existing entries for OTHER skills
    are preserved; entries for the SAME skill are updated.

    Called by ``skill load`` (M4-C) after copying scripts to session_dir.
    """
    data = read_skill_envs(session_dir)
    entries = data.get("entries", {})

    for rel_path in script_rel_paths:
        normalized = _normalize_rel_path(rel_path)
        entries[normalized] = {
            "skill_name": skill_name,
            "skill_version": skill_version,
            "env_bin": env_bin,
        }

    data["version"] = _SCHEMA_VERSION
    data["entries"] = entries
    _write_skill_envs(session_dir, data)


def resolve_env_for_script(
    session_dir: str,
    script_rel_path: str,
    default_env_bin: str,
) -> str:
    """Resolve the effective env bin for a script path.

    If the script is registered in skill_envs.json AND the env_bin exists,
    returns the skill env_bin. Otherwise returns default_env_bin.

    Called by bash/script tools (M4-D) on each execution.
    """
    normalized = _normalize_rel_path(script_rel_path)
    data = read_skill_envs(session_dir)
    entry = data.get("entries", {}).get(normalized)
    if entry:
        env_bin = entry.get("env_bin", "")
        if env_bin and os.path.isdir(env_bin):
            return env_bin
    return default_env_bin


def resolve_env_for_command(
    session_dir: str,
    command: str,
    default_env_bin: str,
) -> str:
    """Resolve the effective env bin by inspecting a bash command string.

    Scans the command for references to any registered script path.
    Returns the matching skill env_bin, or default_env_bin if no match.
    """
    data = read_skill_envs(session_dir)
    entries = data.get("entries", {})
    if not entries:
        return default_env_bin

    for script_path, entry in entries.items():
        if script_path in command:
            env_bin = entry.get("env_bin", "")
            if env_bin and os.path.isdir(env_bin):
                return env_bin

    return default_env_bin


# ---------------------------------------------------------------------------
# Catalog env resolution
# ---------------------------------------------------------------------------


def get_catalog_skill_env_bin(skill_name: str, version: str) -> str | None:
    """Resolve the catalog .venv/bin path for a skill version.

    Returns the absolute path to ``_catalog/skills/<name>/<version>/.venv/bin``
    if it exists, or None otherwise.
    """
    catalog_dir = os.path.join(settings.workspace_root, "_catalog", "skills")
    env_bin = os.path.join(catalog_dir, skill_name, version, ".venv", "bin")
    if os.path.isdir(env_bin):
        return env_bin
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_rel_path(rel_path: str) -> str:
    """Normalize a relative path for consistent mapping keys.

    Strips leading ./ and collapses separators.
    """
    cleaned = os.path.normpath(rel_path)
    # normpath may produce leading ./ on some inputs — strip it
    if cleaned.startswith("." + os.sep):
        cleaned = cleaned[2:]
    return cleaned
