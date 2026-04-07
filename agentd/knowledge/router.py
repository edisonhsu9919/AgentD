"""Knowledge Base REST API (Phase P6-D + P6-E).

Provides endpoints for frontend to:
- List knowledge documents (with permission filtering)
- Get document metadata and content
- Get/download raw source files
- Resolve source references from agent responses
- Import files to knowledge base (system-level workflow)

These endpoints are independent of the agent loop — frontend calls them
directly when rendering source links or the knowledge panel.
"""

import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from auth.models import User
from core.database import get_db
from core.response import ok

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Knowledge catalog (frontend) ────────────────────────────────────────────


@router.get("/documents")
async def list_knowledge_documents(
    q: str = Query("", description="Search query (matches title and tags, case-insensitive)"),
    tag: str = Query("", description="Filter by specific tag (case-insensitive substring)"),
    current_user: User = Depends(get_current_user),
):
    """List knowledge documents visible to the current user.

    Phase 6F: supports search by title/tags and admin sees all.
    Returns metadata only (no content body).
    """
    from knowledge.store import list_knowledge_docs

    # Admin sees all; regular users see public + own private
    if current_user.role == "admin":
        docs = list_knowledge_docs(user_id=None)  # None = all public
        # Admin also needs to see private docs from all users
        docs = _list_all_docs_admin()
    else:
        docs = list_knowledge_docs(user_id=str(current_user.id))

    # Tag filter
    if tag:
        tag_lower = tag.lower()
        docs = [d for d in docs if any(tag_lower in t.lower() for t in d.get("tags", []))]

    # Search filter (title + tags)
    if q:
        q_lower = q.lower()
        docs = [
            d for d in docs
            if q_lower in d.get("title", "").lower()
            or any(q_lower in t.lower() for t in d.get("tags", []))
        ]

    return ok(docs)


def _list_all_docs_admin() -> list[dict]:
    """Admin: list ALL knowledge documents regardless of permission."""
    from knowledge.store import get_files_dir, read_knowledge_doc
    import os as _os

    files_dir = get_files_dir()
    if not _os.path.isdir(files_dir):
        return []

    results = []
    for fname in sorted(_os.listdir(files_dir)):
        if not fname.endswith(".md"):
            continue
        doc_id = fname[:-3]
        result = read_knowledge_doc(doc_id)
        if result is None:
            continue
        fm, _body = result
        fm["doc_id"] = doc_id
        results.append(fm)
    return results


# ── Single document metadata + content ──────────────────────────────────────


@router.get("/documents/{doc_id}")
async def get_knowledge_document(
    doc_id: str,
    include_content: bool = Query(False, description="Include full Markdown body"),
    current_user: User = Depends(get_current_user),
):
    """Get a single knowledge document's metadata and optionally its content.

    Permission check: only returns if document is public or owned by user.
    """
    from knowledge.store import read_knowledge_doc, list_knowledge_docs

    # Permission check
    visible_ids = {d["doc_id"] for d in list_knowledge_docs(user_id=str(current_user.id))}
    if doc_id not in visible_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Document not found or access denied"},
        )

    result = read_knowledge_doc(doc_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Document not found"},
        )

    fm, body = result
    response = {
        "doc_id": doc_id,
        **fm,
    }
    if include_content:
        response["content"] = body
        response["content_lines"] = body.count("\n") + 1

    return ok(response)


# ── Source resolution (for clickable source links) ──────────────────────────


@router.get("/source/{doc_id}")
async def resolve_knowledge_source(
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    """Resolve a knowledge source reference for frontend display.

    Returns:
    - Document metadata (title, kind, author, tags)
    - Raw file path (if original exists)
    - Knowledge Markdown path
    - Whether the raw file is available for preview

    Frontend uses this to decide: open raw file in panel, or fall back to Markdown.
    """
    from knowledge.store import (
        read_knowledge_doc,
        list_knowledge_docs,
        get_raw_file_path,
        get_files_dir,
    )

    # Permission check
    visible_ids = {d["doc_id"] for d in list_knowledge_docs(user_id=str(current_user.id))}
    if doc_id not in visible_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Source not found or access denied"},
        )

    result = read_knowledge_doc(doc_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Source document not found"},
        )

    fm, _body = result
    source_file = fm.get("source_file", "")
    raw_path = get_raw_file_path(source_file) if source_file else None

    return ok({
        "doc_id": doc_id,
        "title": fm.get("title", ""),
        "kind": fm.get("kind", ""),
        "author": fm.get("author", ""),
        "tags": fm.get("tags", []),
        "description": fm.get("description", ""),
        "source_file": source_file,
        "raw_available": raw_path is not None,
        "knowledge_md_path": f"knowledge/files/{doc_id}.md",
        "raw_path": f"knowledge/raw/{source_file}" if source_file else None,
    })


# ── Raw file download/preview ───────────────────────────────────────────────


@router.get("/raw/{filename:path}")
async def download_raw_file(
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """Download or preview an original knowledge source file.

    Permission check: user must have access to at least one knowledge document
    that references this raw file.
    """
    import mimetypes
    from knowledge.store import list_knowledge_docs, get_raw_file_path

    # Permission check: find a visible doc that references this file
    visible_docs = list_knowledge_docs(user_id=str(current_user.id))
    authorized = any(d.get("source_file") == filename for d in visible_docs)

    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "File not found or access denied"},
        )

    raw_path = get_raw_file_path(filename)
    if raw_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Raw file not found"},
        )

    mime, _ = mimetypes.guess_type(raw_path)
    return FileResponse(
        raw_path,
        filename=filename,
        media_type=mime or "application/octet-stream",
    )


# ── Knowledge deletion (Phase P6-F) ─────────────────────────────────────────


@router.delete("/documents/{doc_id}")
async def delete_knowledge_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
):
    """Delete a knowledge document and its raw file.

    Phase 6F: admin can delete any; user can only delete own (owner match).
    Deletes both knowledge/files/{doc_id}.md and knowledge/raw/{source_file}.
    """
    from knowledge.store import read_knowledge_doc, get_files_dir, get_raw_dir

    result = read_knowledge_doc(doc_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Knowledge document not found"},
        )

    fm, _body = result

    # Permission check
    is_admin = current_user.role == "admin"
    is_owner = fm.get("owner") == str(current_user.id)

    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "You can only delete your own knowledge documents"},
        )

    # Delete md file
    md_path = os.path.join(get_files_dir(), f"{doc_id}.md")
    deleted_md = False
    if os.path.isfile(md_path):
        os.remove(md_path)
        deleted_md = True

    # Delete raw file
    source_file = fm.get("source_file", "")
    deleted_raw = False
    if source_file:
        raw_path = os.path.join(get_raw_dir(), source_file)
        if os.path.isfile(raw_path):
            os.remove(raw_path)
            deleted_raw = True

    return ok({
        "deleted": True,
        "doc_id": doc_id,
        "deleted_md": deleted_md,
        "deleted_raw": deleted_raw,
        "source_file": source_file,
    })


# ── System-level knowledge import (Phase P6-E restructured) ─────────────────

# Module-level task registry to prevent GC of background import tasks
_active_import_tasks: dict[str, asyncio.Task] = {}


class KnowledgeImportRequest(BaseModel):
    session_id: str
    source_path: str
    title: str
    description: str = ""
    tags: str = ""
    permission: str = "private"


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def start_knowledge_import(
    body: KnowledgeImportRequest,
    current_user: User = Depends(get_current_user),
):
    """Start a system-level knowledge import after user confirms metadata.

    Frontend form collects title/description/tags/permission, then calls this.
    1. Copies raw file to knowledge/raw/ immediately
    2. Starts background extraction + Markdown generation + commit
    3. Returns task_id for progress polling
    """
    import shutil
    from workspace.manager import get_session_dir, validate_path
    from knowledge.store import ensure_knowledge_dirs, get_raw_dir

    session_dir = get_session_dir(current_user.workspace, body.session_id)

    try:
        abs_path = validate_path(session_dir, body.source_path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid source path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"File not found: {body.source_path}"},
        )

    task_id = str(uuid.uuid4())
    filename = os.path.basename(abs_path)

    # Step 1: Copy raw file immediately (survives session deletion)
    ensure_knowledge_dirs()
    raw_dest = os.path.join(get_raw_dir(), filename)
    if not os.path.isfile(raw_dest):
        shutil.copy2(abs_path, raw_dest)

    ext = os.path.splitext(filename)[1].lower()
    kind_map = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx",
                ".png": "image", ".jpg": "image", ".jpeg": "image",
                ".txt": "text", ".md": "text"}
    kind = kind_map.get(ext, "unknown")

    metadata = {
        "title": body.title,
        "description": body.description,
        "tags": body.tags,
        "permission": body.permission,
        "kind": kind,
    }

    async def publish(sid, event):
        try:
            from core.event_bridge import notify
            await notify(sid, event)
        except Exception:
            pass

    # Step 2: Launch background extraction + commit
    from knowledge.importer import run_import_task

    task = asyncio.create_task(
        run_import_task(
            task_id=task_id,
            session_id=body.session_id,
            session_dir=session_dir,
            user_id=str(current_user.id),
            source_path=abs_path,
            raw_path=raw_dest,
            metadata=metadata,
            publish_fn=publish,
        )
    )
    _active_import_tasks[task_id] = task
    task.add_done_callback(lambda t: _active_import_tasks.pop(task_id, None))

    logger.info("Knowledge import started: task=%s file=%s", task_id[:8], filename)

    return ok({
        "task_id": task_id,
        "status": "launched",
        "filename": filename,
        "kind": kind,
    })


@router.get("/import/{task_id}")
async def get_import_progress(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get progress of a knowledge import task. Frontend polls this."""
    from knowledge.importer import read_import_progress

    sessions_dir = os.path.join(current_user.workspace, "sessions")
    if os.path.isdir(sessions_dir):
        for session_name in os.listdir(sessions_dir):
            session_dir = os.path.join(sessions_dir, session_name)
            progress = read_import_progress(session_dir, task_id)
            if progress:
                return ok(progress)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "Import task not found"},
    )


@router.get("/import-draft")
async def get_import_draft(
    session_id: str = Query(...),
    source_path: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    """Get metadata draft suggestions for a file before import."""
    from workspace.manager import get_session_dir, validate_path
    from knowledge.importer import generate_metadata_draft

    session_dir = get_session_dir(current_user.workspace, session_id)

    try:
        abs_path = validate_path(session_dir, source_path)
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": "Invalid source path"},
        )

    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "File not found"},
        )

    draft = await generate_metadata_draft(abs_path)
    return ok(draft)


@router.get("/imports")
async def list_session_imports(
    session_id: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    """List import tasks for a session. Used to restore progress after panel close.

    Returns the most recent import tasks for this session, newest first.
    Frontend can use this to show active/recent imports in the html_app tab.
    """
    from workspace.manager import get_session_dir
    from knowledge.importer import list_import_tasks

    session_dir = get_session_dir(current_user.workspace, session_id)
    tasks = list_import_tasks(session_dir)
    return ok(tasks)
