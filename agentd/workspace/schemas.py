"""Workspace API Pydantic schemas (Phase 6.7, enhanced Phase G2-G3)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FileNode(BaseModel):
    """Single entry in the file tree."""
    path: str
    name: str
    type: str  # "file" | "dir"
    size: Optional[int] = None
    updated_at: Optional[datetime] = None
    children: Optional[list["FileNode"]] = None


# ── Preview mode enum (Phase G3) ────────────────────────────────────────────

class PreviewMode(str, Enum):
    """Formal preview mode enum — front-end should not invent its own."""
    text = "text"
    image = "image"
    pdf = "pdf"
    office = "office"
    binary = "binary"
    download = "download"


class FileMeta(BaseModel):
    """File metadata for preview decisions (G3 enhanced)."""
    path: str
    name: str
    size: int
    mime_type: str
    extension: str = ""
    is_previewable: bool
    preview_mode: Optional[str] = None
    download_only: bool = False
    updated_at: Optional[datetime] = None
    encoding: Optional[str] = None  # text files only


# ── File management request schemas (Phase G2) ──────────────────────────────

class MkdirRequest(BaseModel):
    path: str = Field(..., description="Relative directory path to create")


class RenameRequest(BaseModel):
    path: str = Field(..., description="Relative path of file/dir to rename")
    new_name: str = Field(..., description="New name (same directory, no path separators)")


class MoveRequest(BaseModel):
    path: str = Field(..., description="Relative path of file/dir to move")
    target_dir: str = Field(..., description="Relative destination directory")


class DeleteRequest(BaseModel):
    path: str = Field(..., description="Relative path of file/dir to delete")


# ── File operation result (Phase G2) ────────────────────────────────────────

class FileOpResult(BaseModel):
    """Unified result for file management operations."""
    path: str
    type: str  # "file" | "dir"
    updated_at: Optional[datetime] = None
