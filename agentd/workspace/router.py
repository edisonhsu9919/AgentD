"""Session-scoped workspace API (Phase 6.7, §5.5; enhanced Phase G2-G4).

All endpoints operate within the session working directory (session_dir).
The frontend file tree root is session_dir — not user_root.

File management (G2): mkdir, rename, move, delete
Preview contract (G3): enhanced meta with extension/download_only/updated_at
Consistency (G4): unified error codes across all endpoints
"""

import mimetypes
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from auth.models import User
from core.database import get_db
from core.response import ok
from session import service as session_svc
from workspace.manager import get_session_dir, is_internal_path, validate_path
from workspace.schemas import (
    DeleteRequest,
    FileMeta,
    FileNode,
    FileOpResult,
    MkdirRequest,
    MoveRequest,
    RenameRequest,
)

router = APIRouter()

# Maximum upload size: 50 MB
_MAX_UPLOAD_SIZE = 500 * 1024 * 1024

# Text-previewable extensions
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".csv", ".xml", ".html", ".css", ".sh", ".bash",
    ".sql", ".env", ".gitignore", ".dockerfile", ".makefile", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
    ".r", ".m", ".lua", ".pl", ".scala", ".log",
}

# Image-previewable extensions
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}

# PDF
_PDF_EXTENSIONS = {".pdf"}

# Office document extensions
_OFFICE_EXTENSIONS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".odt", ".ods", ".odp",
}


async def _get_session_dir(
    session_id: uuid.UUID,
    db: AsyncSession,
    current_user: User,
) -> str:
    """Verify session ownership and return its working directory."""
    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found"},
        )
    return get_session_dir(current_user.workspace, str(session_id))


def _build_tree(root: str, rel_prefix: str = "") -> list[dict]:
    """Recursively build file tree from a directory."""
    entries: list[dict] = []
    try:
        items = sorted(os.listdir(root))
    except OSError:
        return entries

    for name in items:
        # Phase G1: explicitly skip reserved system directories (.agentd)
        if is_internal_path(name):
            continue
        if name.startswith("."):
            continue  # Skip other hidden files (display policy, separate from access isolation)
        full_path = os.path.join(root, name)
        rel_path = os.path.join(rel_prefix, name) if rel_prefix else name
        stat = os.stat(full_path)
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        if os.path.isdir(full_path):
            children = _build_tree(full_path, rel_path)
            entries.append({
                "path": rel_path,
                "name": name,
                "type": "dir",
                "size": None,
                "updated_at": updated.isoformat(),
                "children": children,
            })
        else:
            entries.append({
                "path": rel_path,
                "name": name,
                "type": "file",
                "size": stat.st_size,
                "updated_at": updated.isoformat(),
                "children": None,
            })
    return entries


def _get_preview_info(ext: str) -> tuple[bool, Optional[str], bool]:
    """Determine preview capabilities for a file extension.

    Returns (is_previewable, preview_mode, download_only).
    Phase G3: formalized preview mode enum.
    """
    ext_lower = ext.lower()
    if ext_lower in _TEXT_EXTENSIONS:
        return True, "text", False
    if ext_lower in _IMAGE_EXTENSIONS:
        return True, "image", False
    if ext_lower in _PDF_EXTENSIONS:
        return True, "pdf", False
    if ext_lower in _OFFICE_EXTENSIONS:
        return True, "office", False
    # Unknown type — download only
    return False, "download", True


def _reject_internal(path: str) -> None:
    """Raise 400 if path targets a reserved system directory (Phase G1)."""
    if is_internal_path(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Path points to internal system directory",
            },
        )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/{session_id}/workspace/tree")
async def workspace_tree(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return file tree of the session working directory."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    tree = _build_tree(session_dir)
    return ok(tree)


@router.post("/{session_id}/workspace/upload")
async def workspace_upload(
    session_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    target_dir: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload files to the session working directory."""
    session_dir = await _get_session_dir(session_id, db, current_user)

    # Validate target_dir if provided
    if target_dir:
        _reject_internal(target_dir)
        try:
            target_abs = validate_path(session_dir, target_dir)
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "VALIDATION_ERROR", "message": "Invalid target directory"},
            )
    else:
        target_abs = session_dir

    os.makedirs(target_abs, exist_ok=True)

    uploaded: list[dict] = []
    for f in files:
        if not f.filename:
            continue
        # Sanitize filename — strip path components
        safe_name = os.path.basename(f.filename)
        if not safe_name:
            continue

        content = await f.read()
        if len(content) > _MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "VALIDATION_ERROR", "message": f"File {safe_name} exceeds 50MB limit"},
            )

        dest = os.path.join(target_abs, safe_name)
        with open(dest, "wb") as out:
            out.write(content)
        uploaded.append({"name": safe_name, "size": len(content)})

    return ok({"uploaded": uploaded, "count": len(uploaded)})


@router.get("/{session_id}/workspace/download")
async def workspace_download(
    session_id: uuid.UUID,
    path: str = Query(..., description="Relative path within session directory"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a single file from the session working directory."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    _reject_internal(path)
    try:
        abs_path = validate_path(session_dir, path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"File not found: {path}"},
        )

    return FileResponse(
        abs_path,
        filename=os.path.basename(path),
        media_type="application/octet-stream",
    )


@router.get("/{session_id}/workspace/file")
async def workspace_file(
    session_id: uuid.UUID,
    path: str = Query(..., description="Relative path within session directory"),
    mode: str = Query("text", description="Read mode: text or binary"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Read file content for preview."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    _reject_internal(path)
    try:
        abs_path = validate_path(session_dir, path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"File not found: {path}"},
        )

    if mode == "binary":
        mime, _ = mimetypes.guess_type(abs_path)
        return FileResponse(abs_path, media_type=mime or "application/octet-stream")

    # Text mode
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": str(e)},
        )

    return ok({"path": path, "content": content})


@router.get("/{session_id}/workspace/meta")
async def workspace_meta(
    session_id: uuid.UUID,
    path: str = Query(..., description="Relative path within session directory"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return file metadata for preview decisions."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    _reject_internal(path)
    try:
        abs_path = validate_path(session_dir, path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"File not found: {path}"},
        )

    stat = os.stat(abs_path)
    mime, _ = mimetypes.guess_type(abs_path)
    _, ext = os.path.splitext(abs_path)
    is_previewable, preview_mode, download_only = _get_preview_info(ext)
    updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    meta = FileMeta(
        path=path,
        name=os.path.basename(path),
        size=stat.st_size,
        mime_type=mime or "application/octet-stream",
        extension=ext.lower(),
        is_previewable=is_previewable,
        preview_mode=preview_mode,
        download_only=download_only,
        updated_at=updated,
        encoding="utf-8" if preview_mode == "text" else None,
    )
    return ok(meta.model_dump(mode="json"))


# ── File management endpoints (Phase G2) ─────────────────────────────────────


def _validate_and_resolve(session_dir: str, path: str) -> str:
    """Unified path validation: reject internal + validate boundary (G4)."""
    _reject_internal(path)
    try:
        return validate_path(session_dir, path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": f"Invalid path: {path}"},
        )


@router.post("/{session_id}/workspace/mkdir")
async def workspace_mkdir(
    session_id: uuid.UUID,
    body: MkdirRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a directory within the session working directory."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    abs_path = _validate_and_resolve(session_dir, body.path)

    if os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Path already exists: {body.path}"},
        )

    os.makedirs(abs_path)
    stat = os.stat(abs_path)
    updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return ok(FileOpResult(path=body.path, type="dir", updated_at=updated).model_dump(mode="json"))


@router.post("/{session_id}/workspace/rename")
async def workspace_rename(
    session_id: uuid.UUID,
    body: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a file or directory (same parent directory)."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    abs_path = _validate_and_resolve(session_dir, body.path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Not found: {body.path}"},
        )

    # new_name must not contain path separators
    if "/" in body.new_name or "\\" in body.new_name or body.new_name in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid new name"},
        )

    parent = os.path.dirname(abs_path)
    new_abs = os.path.join(parent, body.new_name)
    new_rel = os.path.join(os.path.dirname(body.path), body.new_name) if os.path.dirname(body.path) else body.new_name

    # Check new name doesn't escape or point to internal
    _reject_internal(new_rel)

    if os.path.exists(new_abs):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Target already exists: {new_rel}"},
        )

    os.rename(abs_path, new_abs)
    entry_type = "dir" if os.path.isdir(new_abs) else "file"
    stat = os.stat(new_abs)
    updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return ok(FileOpResult(path=new_rel, type=entry_type, updated_at=updated).model_dump(mode="json"))


@router.post("/{session_id}/workspace/move")
async def workspace_move(
    session_id: uuid.UUID,
    body: MoveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move a file or directory to a different location within session_dir."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    abs_src = _validate_and_resolve(session_dir, body.path)
    abs_dst_dir = _validate_and_resolve(session_dir, body.target_dir)

    if not os.path.exists(abs_src):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Not found: {body.path}"},
        )

    if not os.path.isdir(abs_dst_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": f"Target is not a directory: {body.target_dir}"},
        )

    name = os.path.basename(abs_src)
    new_abs = os.path.join(abs_dst_dir, name)
    new_rel = os.path.join(body.target_dir, name)

    if os.path.exists(new_abs):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Target already exists: {new_rel}"},
        )

    import shutil
    shutil.move(abs_src, new_abs)
    entry_type = "dir" if os.path.isdir(new_abs) else "file"
    stat = os.stat(new_abs)
    updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return ok(FileOpResult(path=new_rel, type=entry_type, updated_at=updated).model_dump(mode="json"))


@router.delete("/{session_id}/workspace/item")
async def workspace_delete(
    session_id: uuid.UUID,
    path: str = Query(..., description="Relative path of file/dir to delete"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a file or directory from the session working directory."""
    session_dir = await _get_session_dir(session_id, db, current_user)
    abs_path = _validate_and_resolve(session_dir, path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Not found: {path}"},
        )

    # Prevent deleting the session_dir root itself
    if os.path.realpath(abs_path) == os.path.realpath(session_dir):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Cannot delete session root directory"},
        )

    import shutil
    if os.path.isdir(abs_path):
        shutil.rmtree(abs_path)
        entry_type = "dir"
    else:
        os.remove(abs_path)
        entry_type = "file"

    return ok({"deleted": True, "path": path, "type": entry_type})


# ── File inspection endpoint (Phase P1) ────────────────────────────────────

# Extensions that support structured inspection
_INSPECTABLE_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".eml",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
}


@router.get("/{session_id}/workspace/inspect")
async def workspace_inspect(
    session_id: uuid.UUID,
    path: str = Query(..., description="Relative path within session directory"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return structured reconnaissance for a file (Phase P1).

    Directly invokes the same extraction layers used by the file_inspect
    agent tool, but as a REST endpoint independent of the agent loop.
    Frontend calls this when a user clicks a file in the sidebar to
    populate the right-side panel preview.

    Supported: PDF, DOCX, XLSX, PPTX, EML, PNG/JPG/WEBP/BMP/GIF.
    Unsupported extensions return preview_mode + basic metadata only.
    """
    session_dir = await _get_session_dir(session_id, db, current_user)
    _reject_internal(path)
    try:
        abs_path = validate_path(session_dir, path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"File not found: {path}"},
        )

    _, ext = os.path.splitext(abs_path)
    ext = ext.lower()

    if ext not in _INSPECTABLE_EXTENSIONS:
        # Return basic metadata for non-inspectable files
        stat = os.stat(abs_path)
        mime, _ = mimetypes.guess_type(abs_path)
        is_previewable, preview_mode, download_only = _get_preview_info(ext)
        return ok({
            "path": path,
            "kind": "file",
            "inspectable": False,
            "preview_mode": preview_mode,
            "size_bytes": stat.st_size,
            "mime_type": mime or "application/octet-stream",
        })

    # Dispatch to extraction layers
    result = await _extract_file_info(abs_path, ext)
    result["path"] = path
    result["inspectable"] = True
    return ok(result)


async def _extract_file_info(abs_path: str, ext: str) -> dict:
    """Dispatch to the appropriate extraction layer."""
    try:
        if ext == ".pdf":
            from files.pdf import extract
            return extract(abs_path)

        if ext == ".docx":
            from files.office_docx import extract
            return extract(abs_path)

        if ext == ".xlsx":
            from files.office_xlsx import extract
            return extract(abs_path)

        if ext == ".pptx":
            from files.office_pptx import extract
            return extract(abs_path)

        if ext == ".eml":
            from files.email_eml import extract
            return extract(abs_path)

        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            from files.image import extract_metadata
            return extract_metadata(abs_path)

    except FileNotFoundError:
        return {"kind": "error", "message": f"File not found: {abs_path}"}
    except ValueError as e:
        return {"kind": "error", "message": f"Invalid file: {e}"}
    except Exception as e:
        return {"kind": "error", "message": f"Inspection failed: {e}"}

    return {"kind": "unknown", "message": f"No extractor for {ext}"}
