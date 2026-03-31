"""PDF extraction layer (Phase O1).

Pure data extraction from PDF files using pypdf.
No LLM calls — structured raw data only.

Future: add analyze(extract_result, llm) for LLM-enhanced reconnaissance.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Text sample limits
TEXT_SAMPLE_MAX_PAGES = 2
TEXT_SAMPLE_MAX_CHARS = 2000

# Heuristic thresholds for PDF kind classification
_MIN_CHARS_TEXT_PDF = 50        # avg chars/page below this AND low ratio → image-based
_MIXED_RATIO_LOW = 0.3         # below this extractable ratio → image_like
_MIXED_RATIO_HIGH = 0.8        # above this → text_pdf; between → mixed


def extract(path: str) -> dict[str, Any]:
    """Extract structural metadata and text sample from a PDF file.

    Returns a dict with:
      - path, kind, pdf_kind, page_count, size_bytes
      - extractable_text_ratio, avg_chars_per_page
      - metadata (title, author, subject, creator)
      - text_sample (first 1-2 pages, truncated)

    Raises FileNotFoundError if path doesn't exist.
    Raises ValueError if file is not a valid PDF.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    size_bytes = os.path.getsize(path)

    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf") from e

    try:
        reader = PdfReader(path)
    except Exception as e:
        raise ValueError(f"Cannot read PDF: {e}") from e

    page_count = len(reader.pages)

    # Extract metadata
    meta = reader.metadata or {}
    metadata = {
        "title": _clean_meta(meta.get("/Title") or getattr(meta, "title", None)),
        "author": _clean_meta(meta.get("/Author") or getattr(meta, "author", None)),
        "subject": _clean_meta(meta.get("/Subject") or getattr(meta, "subject", None)),
        "creator": _clean_meta(meta.get("/Creator") or getattr(meta, "creator", None)),
    }

    # Extract text from each page
    page_texts: list[str] = []
    pages_with_text = 0

    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        page_texts.append(text)
        if len(text) >= 20:  # at least 20 chars to count as "has text"
            pages_with_text += 1

    # Compute metrics
    total_chars = sum(len(t) for t in page_texts)
    avg_chars = total_chars // page_count if page_count > 0 else 0
    extractable_ratio = pages_with_text / page_count if page_count > 0 else 0.0

    # Classify PDF kind
    pdf_kind = _classify_pdf_kind(avg_chars, extractable_ratio)

    # Build text sample from first N pages
    text_sample = _build_text_sample(page_texts)

    return {
        "path": path,
        "kind": "pdf",
        "pdf_kind": pdf_kind,
        "page_count": page_count,
        "size_bytes": size_bytes,
        "extractable_text_ratio": round(extractable_ratio, 2),
        "avg_chars_per_page": avg_chars,
        "metadata": metadata,
        "text_sample": text_sample,
    }


def _classify_pdf_kind(avg_chars: int, extractable_ratio: float) -> str:
    """Classify PDF as text_pdf, image_like_pdf, or mixed.

    Uses a combination of average chars per page and extractable text ratio.
    Key insight: short text with high ratio = short text PDF, not a scan.
    Only classify as image_like when BOTH chars are very low AND ratio is low.
    """
    # High ratio = genuinely extractable text, even if pages are short
    if extractable_ratio >= _MIXED_RATIO_HIGH:
        # Still need *some* text to call it text_pdf (not completely blank)
        if avg_chars >= _MIN_CHARS_TEXT_PDF:
            return "text_pdf"
        # High ratio but almost no chars (e.g. 10 chars/page) → likely metadata-only
        return "image_like_pdf"
    # Low ratio = most pages have no text
    if extractable_ratio <= _MIXED_RATIO_LOW:
        return "image_like_pdf"
    # Middle ground: some pages have text, some don't
    return "mixed"


def _build_text_sample(page_texts: list[str]) -> str:
    """Build a text sample from the first few pages, capped at TEXT_SAMPLE_MAX_CHARS."""
    parts: list[str] = []
    total = 0

    for i, text in enumerate(page_texts[:TEXT_SAMPLE_MAX_PAGES]):
        if not text:
            continue
        remaining = TEXT_SAMPLE_MAX_CHARS - total
        if remaining <= 0:
            break
        chunk = text[:remaining]
        parts.append(f"[Page {i + 1}]\n{chunk}")
        total += len(chunk)

    return "\n\n".join(parts) if parts else ""


def _clean_meta(value) -> str:
    """Clean a PDF metadata value to plain string."""
    if value is None:
        return ""
    return str(value).strip()
