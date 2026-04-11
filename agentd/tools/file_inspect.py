"""file_inspect tool (Phase O1 + O2 + O3-2).

Structural reconnaissance for non-text files.
O1: PDF | O2: DOCX, XLSX, PPTX, EML + legacy degradation.
O3-2: Image (PNG/JPG/WEBP/BMP/GIF) + scanned-PDF VLM recon.

Returns structured metadata so the agent can decide
whether to continue processing, load a skill, or skip the file.
Image and scanned-PDF inspection may invoke a VLM side-call
(isolated from the main LLM chain).
"""

import json
import logging
import os
from typing import Any

from tools.base import BaseTool, ToolContext
from workspace.manager import is_internal_path, validate_path

logger = logging.getLogger(__name__)

# Supported extensions (full extraction)
_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".eml"}

# Image extensions (VLM recon)
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Legacy extensions (graceful degradation only)
_LEGACY_EXTENSIONS: dict[str, str] = {
    ".doc": "docx",
    ".xls": "xlsx",
    ".ppt": "pptx",
    ".msg": "eml",
}


class FileInspectTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_inspect"

    @property
    def description(self) -> str:
        return (
            "Structural reconnaissance for PDF, Office (DOCX/XLSX/PPTX), email (EML), "
            "and image (PNG/JPG/WEBP/BMP/GIF) files. "
            "ALWAYS use this as the FIRST step when encountering these file types — "
            "do NOT use file_read or bash commands on them. "
            "For documents: returns page/slide/sheet count, text density, headings, sample content. "
            "For images and scanned PDFs: uses VLM to provide visual summary, text detection, "
            "and document type classification. Use the result to decide whether to "
            "proceed with full processing, load a skill, or inform the user about limitations."
        )

    @property
    def metadata(self) -> "ToolMetadata":
        from tools.base import ToolMetadata
        return ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="medium",
            access_scope="session_only",
            mutates_session_state=False,
            max_result_size_chars=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the workspace.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        path: str = kwargs["path"]

        if is_internal_path(path):
            return {"output": "Access denied: path points to internal system directory", "is_error": True}

        try:
            abs_path = validate_path(ctx.workspace_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        if not os.path.isfile(abs_path):
            return {"output": f"File not found: {path}", "is_error": True}

        ext = os.path.splitext(abs_path)[1].lower()

        # Legacy format degradation
        if ext in _LEGACY_EXTENSIONS:
            return _legacy_degradation(path, ext)

        # Image files → VLM recon
        if ext in _IMAGE_EXTENSIONS:
            return await _inspect_image(abs_path, path)

        if ext not in _SUPPORTED_EXTENSIONS:
            all_supported = sorted(_SUPPORTED_EXTENSIONS | _IMAGE_EXTENSIONS)
            return {
                "output": (
                    f"Unsupported file type: {ext or '(no extension)'}. "
                    f"file_inspect currently supports: {', '.join(all_supported)}. "
                    f"Use file_read for text files."
                ),
                "is_error": True,
            }

        # Dispatch by extension
        if ext == ".pdf":
            return await _inspect_pdf(abs_path, path)
        if ext == ".docx":
            return await _inspect_docx(abs_path, path)
        if ext == ".xlsx":
            return await _inspect_xlsx(abs_path, path)
        if ext == ".pptx":
            return await _inspect_pptx(abs_path, path)
        if ext == ".eml":
            return await _inspect_eml(abs_path, path)

        return {"output": f"No handler for extension: {ext}", "is_error": True}


# ── PDF ─────────────────────────────────────────────────────────────────────


async def _inspect_pdf(abs_path: str, rel_path: str) -> dict[str, Any]:
    """PDF reconnaissance via files.pdf extraction layer."""
    try:
        from files.pdf import extract
        result = extract(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": f"Invalid PDF: {e}", "is_error": True}
    except RuntimeError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"PDF inspection failed: {e}", "is_error": True}

    result["path"] = rel_path

    if result["pdf_kind"] == "image_like_pdf":
        # Try VLM reconnaissance on first page
        vlm_result = await _vlm_recon_scanned_pdf(abs_path, result.get("page_count", 0))
        if vlm_result:
            result.update(vlm_result)
            result["understanding_available"] = vlm_result.get("vlm_success", False)
        else:
            result["understanding_available"] = False
            result["message"] = (
                "This PDF appears to be scanned/image-based. "
                "Text extraction is minimal and VLM is not available. "
                "Consider using a specialized skill for deeper analysis."
            )
    else:
        result["understanding_available"] = True

    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── DOCX ────────────────────────────────────────────────────────────────────


async def _inspect_docx(abs_path: str, rel_path: str) -> dict[str, Any]:
    """DOCX reconnaissance via files.office_docx extraction layer."""
    try:
        from files.office_docx import extract
        result = extract(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": f"Invalid DOCX: {e}", "is_error": True}
    except RuntimeError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"DOCX inspection failed: {e}", "is_error": True}

    result["path"] = rel_path
    result["understanding_available"] = True
    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── XLSX ────────────────────────────────────────────────────────────────────


async def _inspect_xlsx(abs_path: str, rel_path: str) -> dict[str, Any]:
    """XLSX reconnaissance via files.office_xlsx extraction layer."""
    try:
        from files.office_xlsx import extract
        result = extract(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": f"Invalid XLSX: {e}", "is_error": True}
    except RuntimeError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"XLSX inspection failed: {e}", "is_error": True}

    result["path"] = rel_path
    result["understanding_available"] = True
    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── PPTX ────────────────────────────────────────────────────────────────────


async def _inspect_pptx(abs_path: str, rel_path: str) -> dict[str, Any]:
    """PPTX reconnaissance via files.office_pptx extraction layer."""
    try:
        from files.office_pptx import extract
        result = extract(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": f"Invalid PPTX: {e}", "is_error": True}
    except RuntimeError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"PPTX inspection failed: {e}", "is_error": True}

    result["path"] = rel_path
    result["understanding_available"] = True
    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── EML ─────────────────────────────────────────────────────────────────────


async def _inspect_eml(abs_path: str, rel_path: str) -> dict[str, Any]:
    """EML reconnaissance via files.email_eml extraction layer."""
    try:
        from files.email_eml import extract
        result = extract(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": f"Invalid EML: {e}", "is_error": True}
    except RuntimeError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"EML inspection failed: {e}", "is_error": True}

    result["path"] = rel_path
    result["understanding_available"] = True
    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── Image ──────────────────────────────────────────────────────────────────


async def _inspect_image(abs_path: str, rel_path: str) -> dict[str, Any]:
    """Image reconnaissance: metadata + optional VLM visual recon."""
    try:
        from files.image import extract_metadata
        result = extract_metadata(abs_path)
    except FileNotFoundError:
        return {"output": f"File not found: {rel_path}", "is_error": True}
    except ValueError as e:
        return {"output": str(e), "is_error": True}
    except Exception as e:
        return {"output": f"Image inspection failed: {e}", "is_error": True}

    result["path"] = rel_path

    # Try VLM reconnaissance
    vlm_cfg = await _resolve_vlm_config()
    if vlm_cfg is None:
        result["understanding_available"] = False
        result["message"] = (
            "VLM is not configured. Basic image metadata is available but "
            "visual understanding (content description, text detection) requires "
            "a vision-language model. Configure a VLM in admin settings."
        )
        result["recommended_next_action"] = "needs_vision"
    else:
        from files.image import recon_with_vlm
        vlm_result = await recon_with_vlm(
            abs_path,
            base_url=vlm_cfg["base_url"],
            api_key=vlm_cfg["api_key"],
            model_id=vlm_cfg["model_id"],
            timeout=vlm_cfg.get("timeout", 30.0),
        )
        result.update(vlm_result)

        if vlm_result.get("vlm_success"):
            result["understanding_available"] = True
            result["vision_model_used"] = vlm_cfg["model_id"]
        else:
            result["understanding_available"] = False
            result["message"] = (
                f"VLM call failed: {vlm_result.get('vlm_error', 'unknown')}. "
                "Basic image metadata is still available."
            )
            result["recommended_next_action"] = "needs_vision"

    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}


# ── Scanned PDF VLM recon ──────────────────────────────────────────────────


async def _vlm_recon_scanned_pdf(abs_path: str, page_count: int) -> dict[str, Any] | None:
    """Run VLM recon on the first page of a scanned/image-like PDF.

    Returns VLM result dict or None if VLM is unavailable.
    """
    vlm_cfg = await _resolve_vlm_config()
    if vlm_cfg is None:
        return None

    # Render first page to a temporary image
    tmp_image = await _render_pdf_page_to_image(abs_path, page_index=0)
    if tmp_image is None:
        return None

    try:
        from files.image import recon_with_vlm

        vlm_result = await recon_with_vlm(
            tmp_image,
            base_url=vlm_cfg["base_url"],
            api_key=vlm_cfg["api_key"],
            model_id=vlm_cfg["model_id"],
            timeout=vlm_cfg.get("timeout", 30.0),
        )
        vlm_result["page_sampled"] = 1
        vlm_result["sample_scope"] = f"page 1 of {page_count}"
        if vlm_result.get("vlm_success"):
            vlm_result["vision_model_used"] = vlm_cfg["model_id"]
        return vlm_result
    finally:
        # Clean up temp image
        try:
            os.unlink(tmp_image)
        except OSError:
            pass


async def _render_pdf_page_to_image(abs_path: str, page_index: int = 0) -> str | None:
    """Render a single PDF page to a temporary PNG file.

    Uses pypdf + PIL: extract images from the page, or fall back to
    a simple page-level rendering via pdf2image/pymupdf if available.
    Returns the temp file path, or None on failure.
    """
    import tempfile

    # Strategy 1: Try pymupdf (fitz) for high-quality rendering
    try:
        import fitz  # pymupdf
        doc = fitz.open(abs_path)
        if page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]
        # Render at 150 DPI for good quality without huge file size
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pix.save(tmp.name)
        doc.close()
        return tmp.name
    except ImportError:
        pass
    except Exception as e:
        logger.warning("pymupdf render failed for %s page %d: %s", abs_path, page_index, e)

    # Strategy 2: Extract embedded images from the page via pypdf
    try:
        from pypdf import PdfReader
        from PIL import Image
        import io

        reader = PdfReader(abs_path)
        if page_index >= len(reader.pages):
            return None
        page = reader.pages[page_index]

        images = page.images
        if not images:
            return None

        # Use the largest image on the page
        largest = max(images, key=lambda img: len(img.data))
        img = Image.open(io.BytesIO(largest.data))

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, format="PNG")
        return tmp.name
    except Exception as e:
        logger.warning("pypdf image extraction failed for %s page %d: %s", abs_path, page_index, e)

    return None


# ── VLM config helper ──────────────────────────────────────────────────────


async def _resolve_vlm_config() -> dict[str, Any] | None:
    """Resolve VLM config from DB or env. Returns dict or None."""
    try:
        from core.database import AsyncSessionLocal
        from model_config.service import resolve_active_vlm_config

        async with AsyncSessionLocal() as db:
            resolved = await resolve_active_vlm_config(db)

        if resolved is None:
            return None

        return {
            "base_url": resolved.base_url,
            "api_key": resolved.api_key,
            "model_id": resolved.model_id,
            "timeout": resolved.timeout_seconds or 30.0,
        }
    except Exception as e:
        logger.warning("Failed to resolve VLM config: %s", e)
        return None


# ── Legacy degradation ──────────────────────────────────────────────────────


def _legacy_degradation(rel_path: str, ext: str) -> dict[str, Any]:
    """Return structured degradation for legacy Office/email formats."""
    modern = _LEGACY_EXTENSIONS[ext]
    kind = "email" if ext == ".msg" else "office"
    sub_kind_key = "email_kind" if ext == ".msg" else "office_kind"

    result = {
        "path": rel_path,
        "kind": kind,
        sub_kind_key: ext.lstrip("."),
        "understanding_available": False,
        "message": (
            f"Legacy format '{ext}' is not supported for structured reconnaissance. "
            f"Convert to '.{modern}' and retry for full inspection."
        ),
    }
    output = json.dumps(result, indent=2, ensure_ascii=False)
    return {"output": output, "is_error": False}
