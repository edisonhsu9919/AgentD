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


# ── Panel Content Protocol (Phase P1) ─────────────────────────────────────


class PanelType(str, Enum):
    """Panel types supported by the right-side workbench."""
    file_preview = "file_preview"
    task_output = "task_output"
    html_app = "html_app"


class PanelContentType(str, Enum):
    """Content rendering mode within a panel."""
    structured = "structured"
    html_sandbox = "html_sandbox"  # v0.4.0: schema only, not implemented


class StructuredWidget(str, Enum):
    """Built-in widget types for structured panel content."""
    table = "table"
    markdown = "markdown"
    image = "image"
    json = "json"
    # Future: chart, form


class StructuredContent(BaseModel):
    """Structured content rendered by built-in frontend widgets."""
    widget: str  # StructuredWidget value
    data: dict  # Widget-specific data payload


class HtmlSandboxContent(BaseModel):
    """HTML content rendered in an isolated iframe sandbox.

    The iframe communicates with the host via postMessage.
    When the user submits a form inside the iframe, the host
    forwards the data to POST /sessions/{id}/panel-submit.

    Fields:
        html: Complete HTML document to render in the iframe.
        height: Iframe height in pixels.
        permissions: iframe sandbox permissions (e.g. ["allow-scripts"]).
        interaction_id: Unique ID for this interaction. The panel-submit
            API uses this to route the response back to the waiting task.
        callback_task_id: If set, the panel-submit result will be written
            to .agentd/tasks/{callback_task_id}/panel_response.json so
            the detached process can pick it up.
    """
    html: str
    height: int = 400
    permissions: list[str] = Field(default_factory=list)
    interaction_id: str = ""
    callback_task_id: str = ""


class PanelContent(BaseModel):
    """Unified panel content protocol.

    Defines the data contract between backend/skill and frontend panel.
    Frontend PanelRouter uses ``type`` to select the rendering mode.

    Usage in SSE ``panel_update`` event:
        {
            "event": "panel_update",
            "panel_type": "file_preview",
            "panel_content": { ... PanelContent dict ... }
        }
    """
    version: str = "1"
    type: str = "structured"  # PanelContentType value
    title: str = ""
    subtitle: Optional[str] = None
    structured: Optional[StructuredContent] = None
    html_sandbox: Optional[HtmlSandboxContent] = None
