import os
import subprocess
from pathlib import Path

from core.config import settings


# ── Internal path classification (Phase G1) ──────────────────────────────────
# Reserved system directories inside session_dir that must not be accessed
# through ordinary workspace API or agent file tools.
_INTERNAL_DIRS = {".agentd"}


def is_internal_path(rel_path: str) -> bool:
    """Return True if *rel_path* points into a reserved system directory.

    Checks whether the first component of the normalised relative path is a
    known internal directory (e.g. ``.agentd``).  This deliberately does NOT
    blanket-hide all dotfiles — ``.env``, ``.gitignore`` etc. remain user-visible.
    """
    # Normalise: strip leading slashes / backslashes, collapse separators
    cleaned = os.path.normpath(rel_path).replace("\\", "/").lstrip("/")
    first = cleaned.split("/")[0]
    return first in _INTERNAL_DIRS


def validate_path(boundary: str, path: str) -> str:
    """
    Resolve `path` relative to `boundary` and verify it stays within the boundary.
    `boundary` is typically session_dir (tool operations) or skills_dir (skill loading).
    Returns the safe absolute path, or raises PermissionError on path-traversal.
    """
    abs_path = os.path.realpath(os.path.join(boundary, path))
    abs_bd = os.path.realpath(boundary)
    if not abs_path.startswith(abs_bd + os.sep) and abs_path != abs_bd:
        raise PermissionError(f"Path escape attempt: {path}")
    return abs_path


def validate_path_dual(
    primary_boundary: str,
    parent_boundary: str | None,
    path: str,
) -> str:
    """Resolve path against primary boundary, fallback to parent boundary.

    Phase 6: child agents can read files from the parent session.
    Tries primary (child session_dir) first, then parent (parent session_dir).
    Both boundaries are validated for path traversal.
    Returns the safe absolute path, or raises PermissionError.
    """
    # Try primary boundary first
    try:
        abs_path = validate_path(primary_boundary, path)
        if os.path.exists(abs_path):
            return abs_path
    except PermissionError:
        pass

    # Try parent boundary if available
    if parent_boundary:
        try:
            abs_path = validate_path(parent_boundary, path)
            if os.path.exists(abs_path):
                return abs_path
        except PermissionError:
            pass

    # Neither boundary has this file — raise from primary
    raise PermissionError(f"Path escape or not found: {path}")


def create_workspace(user_id: str) -> str:
    """
    Create the workspace directory for a user and return its absolute path.
    Idempotent — safe to call multiple times.
    """
    workspace = os.path.join(settings.workspace_root, str(user_id))
    Path(workspace).mkdir(parents=True, exist_ok=True)
    return workspace


def ensure_user_root(user_root: str) -> None:
    """Self-heal: ensure user root directory and subdirectories exist.

    Called on login to recover from accidental deletion.
    """
    os.makedirs(user_root, exist_ok=True)
    os.makedirs(os.path.join(user_root, "sessions"), exist_ok=True)
    os.makedirs(os.path.join(user_root, "skills"), exist_ok=True)


def get_session_dir(user_root: str, session_id: str) -> str:
    """Return the session working directory, creating it if needed."""
    session_dir = os.path.join(user_root, "sessions", session_id)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


# ── Skills catalog operations (delegated to skills.filesystem, Phase F1) ────
# Re-exported here for backward compatibility with existing imports.
from skills.filesystem import (  # noqa: F401
    get_catalog_dir,
    get_skills_dir,
    install_skill_for_user,
    remove_skill_from_catalog,
    uninstall_skill_for_user,
)


def write_skill_to_catalog(name: str, description: str, content: str, tags: list[str]) -> str:
    """Legacy wrapper — builds SkillPackageMeta and delegates to versioned catalog.

    Uses version="0.1.0" for skills created through the old API.
    """
    from skills.filesystem import write_skill_to_catalog as _write_versioned
    from skills.package import SkillPackageMeta

    meta = SkillPackageMeta(name=name, description=description, tags=tags, version="0.1.0")
    return _write_versioned(meta, content)


def create_venv(workspace: str) -> str:
    """
    Create a Python virtual environment at `{workspace}/.venv/` if it does not
    already exist.  Returns the `bin/` directory path.
    """
    venv_path = os.path.join(workspace, ".venv")
    if not os.path.exists(venv_path):
        subprocess.run(
            ["python3", "-m", "venv", venv_path],
            check=True,
            capture_output=True,
        )
    return os.path.join(venv_path, "bin")
