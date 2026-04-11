"""Helpers for child-session runtime metadata."""

import json
import os
from typing import Any


_META_SUBDIR = ".agentd"
_META_FILENAME = "child_session_meta.json"


def _meta_path(session_dir: str) -> str:
    return os.path.join(session_dir, _META_SUBDIR, _META_FILENAME)


def _normalize_tool_names(tool_names: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for tool_name in tool_names or []:
        if not isinstance(tool_name, str):
            continue
        name = tool_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def write_child_session_meta(
    session_dir: str,
    *,
    parent_session_id: str,
    parent_session_dir: str,
    allowed_tools: list[str] | None,
    resolved_tools: list[str] | None,
) -> dict[str, Any]:
    """Persist child-session runtime metadata used across resume/rebuild."""
    data = {
        "version": 1,
        "parent_session_id": parent_session_id,
        "parent_session_dir": parent_session_dir,
        "allowed_tools": _normalize_tool_names(allowed_tools),
        "resolved_tools": _normalize_tool_names(resolved_tools),
    }
    os.makedirs(os.path.join(session_dir, _META_SUBDIR), exist_ok=True)
    with open(_meta_path(session_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def read_child_session_meta(session_dir: str) -> dict[str, Any]:
    """Load child-session runtime metadata from disk."""
    path = _meta_path(session_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
