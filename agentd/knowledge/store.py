"""Knowledge Store — file system knowledge base (Phase P6-A).

Manages the system-level knowledge directory:

    {workspace_root}/knowledge/
        files/          — Markdown knowledge documents with YAML frontmatter
        raw/            — Original source files (PDF, DOCX, images, etc.)

Knowledge documents are Markdown files with YAML frontmatter containing
metadata (title, description, tags, kind, permission, owner, source info).

The raw/ directory preserves original files for panel preview and download.
The files/ directory contains the extracted/converted Markdown for agent
consumption and search.

Permission model (first version):
  - public: visible to all users
  - private: visible only to owner
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.config import settings

logger = logging.getLogger(__name__)

# ── Directory structure ─────────────────────────────────────────────────────

KNOWLEDGE_ROOT = "knowledge"
FILES_DIR = "files"
RAW_DIR = "raw"


def get_knowledge_root() -> str:
    """Return the absolute path to the system knowledge directory."""
    return os.path.join(settings.workspace_root, KNOWLEDGE_ROOT)


def get_files_dir() -> str:
    """Return the absolute path to knowledge/files/."""
    return os.path.join(get_knowledge_root(), FILES_DIR)


def get_raw_dir() -> str:
    """Return the absolute path to knowledge/raw/."""
    return os.path.join(get_knowledge_root(), RAW_DIR)


def ensure_knowledge_dirs() -> None:
    """Create knowledge directory structure if it doesn't exist."""
    os.makedirs(get_files_dir(), exist_ok=True)
    os.makedirs(get_raw_dir(), exist_ok=True)


# ── Document ID ─────────────────────────────────────────────────────────────

def generate_doc_id() -> str:
    """Generate a unique document ID."""
    return uuid.uuid4().hex[:12]


# ── Frontmatter schema ──────────────────────────────────────────────────────

REQUIRED_FRONTMATTER = {"title", "description", "kind", "permission", "owner"}

FRONTMATTER_DEFAULTS = {
    "tags": [],
    "permission": "private",
    "author": "",
    "created_at": "",
    "source_file": "",
    "source_path": "",
    "file_size": 0,
}


def build_frontmatter(
    *,
    title: str,
    description: str,
    kind: str,
    owner: str,
    permission: str = "private",
    tags: list[str] | None = None,
    author: str = "",
    source_file: str = "",
    source_path: str = "",
    file_size: int = 0,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Build a complete frontmatter dict for a knowledge document."""
    fm: dict[str, Any] = {
        "title": title,
        "description": description[:500],  # Cap description length
        "tags": tags or [],
        "kind": kind,
        "permission": permission,
        "owner": owner,
        "author": author,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "source_path": source_path,
        "file_size": file_size,
    }
    if extra:
        fm.update(extra)
    return fm


def validate_frontmatter(fm: dict) -> list[str]:
    """Validate frontmatter dict. Returns list of error messages (empty = valid)."""
    errors = []
    for key in REQUIRED_FRONTMATTER:
        if not fm.get(key):
            errors.append(f"Missing required field: {key}")
    if fm.get("permission") not in ("public", "private"):
        errors.append(f"Invalid permission: {fm.get('permission')} (must be public or private)")
    return errors


# ── Knowledge document read/write ───────────────────────────────────────────

def write_knowledge_doc(
    doc_id: str,
    frontmatter: dict[str, Any],
    body: str,
) -> str:
    """Write a knowledge Markdown document with YAML frontmatter.

    Creates: knowledge/files/{doc_id}.md
    Returns the absolute file path.
    """
    ensure_knowledge_dirs()

    content = f"---\n{yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)}---\n\n{body}"

    path = os.path.join(get_files_dir(), f"{doc_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path


def read_knowledge_doc(doc_id: str) -> tuple[dict[str, Any], str] | None:
    """Read a knowledge document. Returns (frontmatter, body) or None."""
    path = os.path.join(get_files_dir(), f"{doc_id}.md")
    if not os.path.isfile(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    return parse_knowledge_doc(content)


def parse_knowledge_doc(content: str) -> tuple[dict[str, Any], str] | None:
    """Parse a knowledge document string into (frontmatter, body)."""
    if not content.startswith("---"):
        return None

    # Find the closing ---
    end = content.find("---", 3)
    if end == -1:
        return None

    fm_text = content[3:end].strip()
    body = content[end + 3:].strip()

    try:
        fm = yaml.safe_load(fm_text)
        if not isinstance(fm, dict):
            return None
    except yaml.YAMLError:
        return None

    return fm, body


def list_knowledge_docs(user_id: str | None = None) -> list[dict[str, Any]]:
    """List all knowledge documents visible to a user.

    Permission filter:
    - public docs: always visible
    - private docs: only if owner == user_id

    Returns list of frontmatter dicts with doc_id added.
    """
    files_dir = get_files_dir()
    if not os.path.isdir(files_dir):
        return []

    results = []
    for fname in sorted(os.listdir(files_dir)):
        if not fname.endswith(".md"):
            continue

        doc_id = fname[:-3]  # strip .md
        result = read_knowledge_doc(doc_id)
        if result is None:
            continue

        fm, _body = result

        # Permission check
        permission = fm.get("permission", "private")
        owner = fm.get("owner", "")

        if permission == "public":
            pass  # visible to all
        elif permission == "private":
            if not user_id or owner != user_id:
                continue  # no user or not the owner → skip
        else:
            continue  # unknown permission → skip

        fm["doc_id"] = doc_id
        results.append(fm)

    return results


def save_raw_file(filename: str, content: bytes) -> str:
    """Save an original file to knowledge/raw/.

    Returns the absolute path.
    """
    ensure_knowledge_dirs()
    path = os.path.join(get_raw_dir(), filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


def get_raw_file_path(filename: str) -> str | None:
    """Get the absolute path of a raw file, or None if not found."""
    path = os.path.join(get_raw_dir(), filename)
    return path if os.path.isfile(path) else None


def get_doc_source_path(doc_id: str) -> str | None:
    """Get the source raw file path for a knowledge document."""
    result = read_knowledge_doc(doc_id)
    if result is None:
        return None
    fm, _ = result
    source_file = fm.get("source_file", "")
    if source_file:
        return get_raw_file_path(source_file)
    return None
