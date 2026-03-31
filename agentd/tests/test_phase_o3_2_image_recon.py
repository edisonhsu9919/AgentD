"""Phase O3-2 — Image & Scanned-PDF Reconnaissance tests.

Tests cover:
- files/image.py: image metadata extraction + VLM recon
- tools/file_inspect.py: image routing + scanned-PDF VLM + degradation
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.base import ToolContext


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(session_dir: str) -> ToolContext:
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=session_dir,
        session_dir=session_dir,
        venv_bin="",
        publish=AsyncMock(),
    )


def _create_test_image(path: str, fmt: str = "PNG", size: tuple = (100, 50)) -> str:
    """Create a minimal test image."""
    from PIL import Image
    img = Image.new("RGB", size, color=(255, 0, 0))
    img.save(path, format=fmt)
    return path


# ── files/image.py — extract_metadata ────────────────────────────────────


class TestImageExtractMetadata:
    def test_basic_png(self, tmp_path):
        img_path = str(tmp_path / "test.png")
        _create_test_image(img_path, "PNG", (200, 100))

        from files.image import extract_metadata
        result = extract_metadata(img_path)

        assert result["kind"] == "image"
        assert result["image_format"] == "png"
        assert result["width"] == 200
        assert result["height"] == 100
        assert result["size_bytes"] > 0
        assert "image/png" in result["mime_type"]

    def test_jpeg(self, tmp_path):
        img_path = str(tmp_path / "test.jpg")
        _create_test_image(img_path, "JPEG", (320, 240))

        from files.image import extract_metadata
        result = extract_metadata(img_path)

        assert result["kind"] == "image"
        assert result["image_format"] == "jpeg"
        assert result["width"] == 320
        assert result["height"] == 240

    def test_bmp(self, tmp_path):
        img_path = str(tmp_path / "test.bmp")
        _create_test_image(img_path, "BMP", (64, 64))

        from files.image import extract_metadata
        result = extract_metadata(img_path)

        assert result["kind"] == "image"
        assert result["image_format"] == "bmp"
        assert result["width"] == 64

    def test_webp(self, tmp_path):
        img_path = str(tmp_path / "test.webp")
        _create_test_image(img_path, "WEBP", (80, 60))

        from files.image import extract_metadata
        result = extract_metadata(img_path)

        assert result["kind"] == "image"
        assert result["width"] == 80

    def test_gif(self, tmp_path):
        img_path = str(tmp_path / "test.gif")
        _create_test_image(img_path, "GIF", (32, 32))

        from files.image import extract_metadata
        result = extract_metadata(img_path)

        assert result["kind"] == "image"
        assert result["width"] == 32

    def test_file_not_found(self):
        from files.image import extract_metadata
        with pytest.raises(FileNotFoundError):
            extract_metadata("/nonexistent/image.png")

    def test_unsupported_extension(self, tmp_path):
        bad = tmp_path / "test.tiff"
        bad.write_bytes(b"fake")

        from files.image import extract_metadata
        with pytest.raises(ValueError, match="Unsupported"):
            extract_metadata(str(bad))


# ── files/image.py — recon_with_vlm ─────────────────────────────────────


class TestReconWithVLM:
    @pytest.mark.asyncio
    async def test_vlm_success_json(self, tmp_path):
        img_path = str(tmp_path / "test.png")
        _create_test_image(img_path)

        vlm_json = json.dumps({
            "contains_text": True,
            "document_guess": "receipt",
            "visual_summary": "A scanned receipt",
            "key_elements": ["total", "date"],
            "language_detected": "en",
        })

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.content = vlm_json

        with patch("vlm.provider.describe_image", return_value=mock_resp):
            from files.image import recon_with_vlm
            result = await recon_with_vlm(
                img_path,
                base_url="http://fake",
                api_key="key",
                model_id="test-vlm",
            )

        assert result["vlm_success"] is True
        assert result["contains_text"] is True
        assert result["document_guess"] == "receipt"
        assert "total" in result["key_elements"]

    @pytest.mark.asyncio
    async def test_vlm_success_non_json(self, tmp_path):
        """VLM returns plain text instead of JSON — still works."""
        img_path = str(tmp_path / "test.png")
        _create_test_image(img_path)

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.content = "This is a photo of a cat."

        with patch("vlm.provider.describe_image", return_value=mock_resp):
            from files.image import recon_with_vlm
            result = await recon_with_vlm(
                img_path,
                base_url="http://fake",
                api_key="key",
                model_id="test-vlm",
            )

        assert result["vlm_success"] is True
        assert result["document_guess"] == "unknown"
        assert "cat" in result["visual_summary"]

    @pytest.mark.asyncio
    async def test_vlm_failure(self, tmp_path):
        img_path = str(tmp_path / "test.png")
        _create_test_image(img_path)

        mock_resp = MagicMock()
        mock_resp.success = False
        mock_resp.content = "timeout"

        with patch("vlm.provider.describe_image", return_value=mock_resp):
            from files.image import recon_with_vlm
            result = await recon_with_vlm(
                img_path,
                base_url="http://fake",
                api_key="key",
                model_id="test-vlm",
            )

        assert result["vlm_success"] is False
        assert result["vlm_error"] == "timeout"


# ── file_inspect tool — image routing ────────────────────────────────────


class TestFileInspectImage:
    @pytest.mark.asyncio
    async def test_image_with_vlm(self, tmp_path):
        """Image file with VLM available → full recon result."""
        img_path = str(tmp_path / "photo.png")
        _create_test_image(img_path, "PNG", (640, 480))

        vlm_cfg = {
            "base_url": "http://fake",
            "api_key": "key",
            "model_id": "test-vlm",
            "timeout": 30.0,
        }
        vlm_result = {
            "vlm_success": True,
            "contains_text": False,
            "document_guess": "photo",
            "visual_summary": "A red rectangle",
            "key_elements": [],
            "language_detected": "none",
        }

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("tools.file_inspect._resolve_vlm_config", return_value=vlm_cfg), \
             patch("files.image.recon_with_vlm", return_value=vlm_result) as mock_recon:
            result = await tool.execute(ctx, path="photo.png")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["kind"] == "image"
        assert data["understanding_available"] is True
        assert data["vision_model_used"] == "test-vlm"
        assert data["document_guess"] == "photo"
        assert data["width"] == 640

    @pytest.mark.asyncio
    async def test_image_no_vlm(self, tmp_path):
        """Image file with no VLM → graceful degradation."""
        img_path = str(tmp_path / "photo.jpg")
        _create_test_image(img_path, "JPEG", (320, 240))

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("tools.file_inspect._resolve_vlm_config", return_value=None):
            result = await tool.execute(ctx, path="photo.jpg")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["kind"] == "image"
        assert data["understanding_available"] is False
        assert data["recommended_next_action"] == "needs_vision"
        assert "VLM" in data["message"]
        # Basic metadata still present
        assert data["width"] == 320
        assert data["height"] == 240

    @pytest.mark.asyncio
    async def test_image_vlm_call_fails(self, tmp_path):
        """Image file with VLM configured but call fails → degradation."""
        img_path = str(tmp_path / "photo.webp")
        _create_test_image(img_path, "WEBP", (100, 100))

        vlm_cfg = {
            "base_url": "http://fake",
            "api_key": "key",
            "model_id": "test-vlm",
            "timeout": 30.0,
        }
        vlm_result = {
            "vlm_success": False,
            "vlm_error": "connection refused",
        }

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("tools.file_inspect._resolve_vlm_config", return_value=vlm_cfg), \
             patch("files.image.recon_with_vlm", return_value=vlm_result):
            result = await tool.execute(ctx, path="photo.webp")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["kind"] == "image"
        assert data["understanding_available"] is False
        assert data["recommended_next_action"] == "needs_vision"

    @pytest.mark.asyncio
    async def test_image_file_not_found(self, tmp_path):
        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        result = await tool.execute(ctx, path="nonexistent.png")
        assert result["is_error"]

    @pytest.mark.asyncio
    async def test_all_image_extensions_routed(self, tmp_path):
        """All image extensions are correctly routed to image handler."""
        from tools.file_inspect import _IMAGE_EXTENSIONS
        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        for ext in _IMAGE_EXTENSIONS:
            fname = f"test{ext}"
            fmt_map = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                       ".gif": "GIF", ".webp": "WEBP", ".bmp": "BMP"}
            _create_test_image(str(tmp_path / fname), fmt_map[ext])

            with patch("tools.file_inspect._resolve_vlm_config", return_value=None):
                result = await tool.execute(ctx, path=fname)

            assert not result["is_error"], f"Failed for {ext}"
            data = json.loads(result["output"])
            assert data["kind"] == "image", f"Wrong kind for {ext}"


# ── file_inspect tool — scanned PDF VLM recon ─────────────────────────────


class TestFileInspectScannedPDF:
    @pytest.mark.asyncio
    async def test_scanned_pdf_with_vlm(self, tmp_path):
        """image_like_pdf with VLM → page-sampled recon."""
        pdf_path = str(tmp_path / "scan.pdf")

        # Create a minimal PDF that will be classified as image_like
        mock_extract = {
            "kind": "pdf",
            "pdf_kind": "image_like_pdf",
            "page_count": 3,
            "size_bytes": 500000,
            "extractable_text_ratio": 0.0,
            "avg_chars_per_page": 0,
            "metadata": {"title": "", "author": "", "subject": "", "creator": ""},
            "text_sample": "",
        }

        vlm_cfg = {
            "base_url": "http://fake",
            "api_key": "key",
            "model_id": "test-vlm",
            "timeout": 30.0,
        }
        vlm_result = {
            "vlm_success": True,
            "contains_text": True,
            "document_guess": "invoice",
            "visual_summary": "A scanned invoice",
            "key_elements": ["total", "date", "vendor"],
            "language_detected": "en",
            "page_sampled": 1,
            "sample_scope": "page 1 of 3",
            "vision_model_used": "test-vlm",
        }

        # Need a real file for os.path.isfile check
        from PIL import Image
        img = Image.new("RGB", (100, 100))
        img.save(pdf_path, format="PNG")  # fake "pdf" file for path check

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("files.pdf.extract", return_value=mock_extract), \
             patch("tools.file_inspect._vlm_recon_scanned_pdf", return_value=vlm_result):
            result = await tool.execute(ctx, path="scan.pdf")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["pdf_kind"] == "image_like_pdf"
        assert data["understanding_available"] is True
        assert data["page_sampled"] == 1
        assert data["document_guess"] == "invoice"
        assert data["vision_model_used"] == "test-vlm"

    @pytest.mark.asyncio
    async def test_scanned_pdf_no_vlm(self, tmp_path):
        """image_like_pdf with no VLM → structured degradation."""
        pdf_path = str(tmp_path / "scan.pdf")

        mock_extract = {
            "kind": "pdf",
            "pdf_kind": "image_like_pdf",
            "page_count": 5,
            "size_bytes": 1000000,
            "extractable_text_ratio": 0.0,
            "avg_chars_per_page": 0,
            "metadata": {"title": "", "author": "", "subject": "", "creator": ""},
            "text_sample": "",
        }

        from PIL import Image
        img = Image.new("RGB", (100, 100))
        img.save(pdf_path, format="PNG")

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("files.pdf.extract", return_value=mock_extract), \
             patch("tools.file_inspect._vlm_recon_scanned_pdf", return_value=None):
            result = await tool.execute(ctx, path="scan.pdf")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["pdf_kind"] == "image_like_pdf"
        assert data["understanding_available"] is False
        assert "VLM" in data["message"]

    @pytest.mark.asyncio
    async def test_text_pdf_unchanged(self, tmp_path):
        """text_pdf still works without VLM involvement."""
        pdf_path = str(tmp_path / "text.pdf")

        mock_extract = {
            "kind": "pdf",
            "pdf_kind": "text_pdf",
            "page_count": 10,
            "size_bytes": 50000,
            "extractable_text_ratio": 0.95,
            "avg_chars_per_page": 2000,
            "metadata": {"title": "Test", "author": "", "subject": "", "creator": ""},
            "text_sample": "Hello world",
        }

        from PIL import Image
        img = Image.new("RGB", (100, 100))
        img.save(pdf_path, format="PNG")

        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        ctx = _make_ctx(str(tmp_path))

        with patch("files.pdf.extract", return_value=mock_extract):
            result = await tool.execute(ctx, path="text.pdf")

        assert not result["is_error"]
        data = json.loads(result["output"])
        assert data["pdf_kind"] == "text_pdf"
        assert data["understanding_available"] is True


# ── VLM config resolution ────────────────────────────────────────────────


class TestResolveVLMConfig:
    @pytest.mark.asyncio
    async def test_resolve_from_db(self):
        mock_resolved = MagicMock()
        mock_resolved.base_url = "http://vlm-api"
        mock_resolved.api_key = "secret"
        mock_resolved.model_id = "qwen-vl"
        mock_resolved.timeout_seconds = 45

        mock_db = AsyncMock()
        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database.AsyncSessionLocal", mock_session_cls), \
             patch("model_config.service.resolve_active_vlm_config", return_value=mock_resolved):
            from tools.file_inspect import _resolve_vlm_config
            result = await _resolve_vlm_config()

        assert result is not None
        assert result["base_url"] == "http://vlm-api"
        assert result["model_id"] == "qwen-vl"
        assert result["timeout"] == 45

    @pytest.mark.asyncio
    async def test_resolve_none(self):
        mock_db = AsyncMock()
        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("core.database.AsyncSessionLocal", mock_session_cls), \
             patch("model_config.service.resolve_active_vlm_config", return_value=None):
            from tools.file_inspect import _resolve_vlm_config
            result = await _resolve_vlm_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_exception_returns_none(self):
        """DB error → returns None gracefully."""
        with patch("core.database.AsyncSessionLocal", side_effect=Exception("db down")):
            from tools.file_inspect import _resolve_vlm_config
            result = await _resolve_vlm_config()

        assert result is None


# ── Render PDF page helper ───────────────────────────────────────────────


class TestRenderPDFPage:
    @pytest.mark.asyncio
    async def test_pymupdf_fallback_to_pypdf(self, tmp_path):
        """When pymupdf is not installed, falls back to pypdf image extraction."""
        from tools.file_inspect import _render_pdf_page_to_image

        # If neither pymupdf nor embedded images, returns None
        with patch.dict("sys.modules", {"fitz": None}):
            # Create a minimal PDF with no images
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            pdf_path = str(tmp_path / "blank.pdf")
            with open(pdf_path, "wb") as f:
                writer.write(f)

            result = await _render_pdf_page_to_image(pdf_path, page_index=0)
            # Blank PDF with no images → None
            assert result is None


# ── Description & schema ─────────────────────────────────────────────────


class TestFileInspectMeta:
    def test_description_mentions_image(self):
        from tools.file_inspect import FileInspectTool
        tool = FileInspectTool()
        desc = tool.description
        assert "image" in desc.lower() or "PNG" in desc
        assert "VLM" in desc or "visual" in desc.lower()

    def test_supported_extensions(self):
        from tools.file_inspect import _SUPPORTED_EXTENSIONS, _IMAGE_EXTENSIONS
        # Document extensions
        assert ".pdf" in _SUPPORTED_EXTENSIONS
        assert ".docx" in _SUPPORTED_EXTENSIONS
        # Image extensions
        assert ".png" in _IMAGE_EXTENSIONS
        assert ".jpg" in _IMAGE_EXTENSIONS
        assert ".webp" in _IMAGE_EXTENSIONS
        assert ".bmp" in _IMAGE_EXTENSIONS
        assert ".gif" in _IMAGE_EXTENSIONS
