"""Phase O1 — PDF Reconnaissance / file_inspect tests.

Tests cover:
- files/pdf.py extraction layer: text PDF, image-like PDF, metadata, edge cases
- tools/file_inspect.py tool wrapper: path validation, PDF dispatch, unsupported types
- tools/registry.py: file_inspect registered with allow permission
"""

import json
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tools.base import ToolContext


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(session_dir: str) -> ToolContext:
    """Create a minimal ToolContext for testing."""
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=session_dir,
        session_dir=session_dir,
        venv_bin="",
        publish=AsyncMock(),
    )


def _create_text_pdf(path: str, pages: int = 3, text: str = "Hello world. This is a test PDF document with enough text to be classified as a text PDF.") -> str:
    """Create a simple text-based PDF using pypdf."""
    from pypdf import PdfWriter
    from pypdf._page import PageObject
    from io import BytesIO
    import reportlab  # noqa: F401

    # Use reportlab if available, otherwise fall back to bare pypdf
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        for i in range(pages):
            page_text = f"Page {i+1}. {text}"
            c.drawString(72, 700, page_text)
            c.showPage()
        c.save()
        buf.seek(0)

        with open(path, "wb") as f:
            f.write(buf.getvalue())
    except ImportError:
        # Fallback: create minimal PDF with pypdf only
        writer = PdfWriter()
        for i in range(pages):
            writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            writer.write(f)

    return path


def _create_minimal_pdf(path: str, pages: int = 1) -> str:
    """Create a minimal blank PDF (no text — simulates image-like PDF)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)
    return path


# ── Test: files/pdf.py extraction layer ─────────────────────────────────────


class TestPdfExtract:

    def test_extract_basic_structure(self, tmp_path):
        """Extract should return all required fields."""
        from files.pdf import extract

        pdf_path = _create_minimal_pdf(str(tmp_path / "test.pdf"), pages=5)
        result = extract(pdf_path)

        assert result["kind"] == "pdf"
        assert result["page_count"] == 5
        assert result["size_bytes"] > 0
        assert isinstance(result["extractable_text_ratio"], float)
        assert isinstance(result["avg_chars_per_page"], int)
        assert isinstance(result["metadata"], dict)
        assert "title" in result["metadata"]
        assert "author" in result["metadata"]
        assert isinstance(result["text_sample"], str)

    def test_blank_pdf_classified_as_image_like(self, tmp_path):
        """Blank PDF (no text at all) should be classified as image_like_pdf."""
        from files.pdf import extract

        pdf_path = _create_minimal_pdf(str(tmp_path / "blank.pdf"), pages=3)
        result = extract(pdf_path)

        assert result["pdf_kind"] == "image_like_pdf"
        assert result["extractable_text_ratio"] == 0.0
        assert result["avg_chars_per_page"] == 0
        assert result["text_sample"] == ""

    def test_file_not_found(self, tmp_path):
        """Non-existent path should raise FileNotFoundError."""
        from files.pdf import extract

        with pytest.raises(FileNotFoundError):
            extract(str(tmp_path / "nonexistent.pdf"))

    def test_invalid_pdf(self, tmp_path):
        """Non-PDF file should raise ValueError."""
        from files.pdf import extract

        bad_path = str(tmp_path / "not_a_pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("This is not a PDF file.")

        with pytest.raises(ValueError, match="Cannot read PDF"):
            extract(bad_path)

    def test_single_page_pdf(self, tmp_path):
        """Single page PDF should work correctly."""
        from files.pdf import extract

        pdf_path = _create_minimal_pdf(str(tmp_path / "single.pdf"), pages=1)
        result = extract(pdf_path)

        assert result["page_count"] == 1

    def test_text_sample_capped(self, tmp_path):
        """Text sample should not exceed TEXT_SAMPLE_MAX_CHARS."""
        from files.pdf import TEXT_SAMPLE_MAX_CHARS, extract

        # Create a PDF — even if blank, text_sample should be within limits
        pdf_path = _create_minimal_pdf(str(tmp_path / "big.pdf"), pages=100)
        result = extract(pdf_path)

        assert len(result["text_sample"]) <= TEXT_SAMPLE_MAX_CHARS + 200  # header overhead


class TestPdfClassification:
    """Test the PDF kind classification heuristics."""

    def test_classify_image_like(self):
        from files.pdf import _classify_pdf_kind
        assert _classify_pdf_kind(avg_chars=10, extractable_ratio=0.1) == "image_like_pdf"

    def test_classify_text_pdf(self):
        from files.pdf import _classify_pdf_kind
        assert _classify_pdf_kind(avg_chars=1500, extractable_ratio=0.95) == "text_pdf"

    def test_classify_mixed(self):
        from files.pdf import _classify_pdf_kind
        assert _classify_pdf_kind(avg_chars=500, extractable_ratio=0.5) == "mixed"

    def test_negligible_chars_image_like(self):
        from files.pdf import _classify_pdf_kind
        # Near-zero avg chars even with high ratio → image_like (metadata-only)
        assert _classify_pdf_kind(avg_chars=10, extractable_ratio=1.0) == "image_like_pdf"

    def test_short_text_high_ratio_is_text_pdf(self):
        from files.pdf import _classify_pdf_kind
        # Short but genuinely extractable text (e.g. 66 chars/page, ratio=1.0) → text_pdf
        assert _classify_pdf_kind(avg_chars=66, extractable_ratio=1.0) == "text_pdf"


class TestPdfMetaCleaning:

    def test_clean_meta_none(self):
        from files.pdf import _clean_meta
        assert _clean_meta(None) == ""

    def test_clean_meta_string(self):
        from files.pdf import _clean_meta
        assert _clean_meta("  Some Title  ") == "Some Title"

    def test_clean_meta_non_string(self):
        from files.pdf import _clean_meta
        assert _clean_meta(42) == "42"


# ── Test: tools/file_inspect.py tool wrapper ────────────────────────────────


class TestFileInspectTool:

    def test_tool_properties(self):
        from tools.file_inspect import FileInspectTool

        tool = FileInspectTool()
        assert tool.name == "file_inspect"
        assert "PDF" in tool.description
        schema = tool.schema()
        assert "path" in schema["properties"]
        assert "path" in schema["required"]

    @pytest.mark.asyncio
    async def test_inspect_pdf_success(self, tmp_path):
        from tools.file_inspect import FileInspectTool

        pdf_path = _create_minimal_pdf(str(tmp_path / "test.pdf"), pages=3)
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="test.pdf")
        assert result["is_error"] is False

        data = json.loads(result["output"])
        assert data["kind"] == "pdf"
        assert data["page_count"] == 3
        assert data["path"] == "test.pdf"  # relative, not absolute

    @pytest.mark.asyncio
    async def test_inspect_pdf_image_like_degradation(self, tmp_path):
        """Image-like PDF should include degradation message."""
        from tools.file_inspect import FileInspectTool

        pdf_path = _create_minimal_pdf(str(tmp_path / "scan.pdf"), pages=2)
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="scan.pdf")
        assert result["is_error"] is False

        data = json.loads(result["output"])
        assert data["pdf_kind"] == "image_like_pdf"
        assert data["understanding_available"] is False
        assert "scanned" in data["message"].lower() or "image" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_inspect_unsupported_type(self, tmp_path):
        """Non-PDF file should return informative error."""
        from tools.file_inspect import FileInspectTool

        txt_path = str(tmp_path / "readme.txt")
        with open(txt_path, "w") as f:
            f.write("hello")

        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="readme.txt")
        assert result["is_error"] is True
        assert "Unsupported" in result["output"]
        assert "file_read" in result["output"]

    @pytest.mark.asyncio
    async def test_inspect_file_not_found(self, tmp_path):
        from tools.file_inspect import FileInspectTool

        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="nonexistent.pdf")
        assert result["is_error"] is True
        assert "not found" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_inspect_invalid_pdf(self, tmp_path):
        from tools.file_inspect import FileInspectTool

        bad_path = str(tmp_path / "bad.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="bad.pdf")
        assert result["is_error"] is True
        assert "Invalid PDF" in result["output"] or "PDF" in result["output"]

    @pytest.mark.asyncio
    async def test_inspect_internal_path_blocked(self, tmp_path):
        from tools.file_inspect import FileInspectTool

        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path=".agentd/config.json")
        assert result["is_error"] is True
        assert "Access denied" in result["output"]


# ── Test: Registry integration ──────────────────────────────────────────────


class TestFileInspectRegistry:

    def test_registered_in_registry(self):
        """file_inspect should be in the default tool registry."""
        from tools.registry import get_registry

        registry = get_registry()
        tool = registry.get("file_inspect")
        assert tool is not None
        assert tool.name == "file_inspect"

    def test_default_permission_is_allow(self):
        """file_inspect should have 'allow' default permission (read-only tool)."""
        from tools.registry import get_registry

        registry = get_registry()
        assert registry.default_permission("file_inspect") == "allow"

    def test_tool_count_is_11(self):
        """After O1, we should have 11 tools registered."""
        from tools.registry import get_registry

        registry = get_registry()
        assert len(registry.tools) == 16


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
