import os
from pathlib import Path
from typing import Any, Optional

import aiofiles

from tools.base import BaseTool, ToolContext
from workspace.manager import is_internal_path, validate_path


_STRUCTURED_OR_BINARY_EXTENSIONS = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bin",
}
_TEXT_ERROR = (
    "file_read only supports plain text files. This file appears to be "
    "Office/PDF/archive/image/binary content. Use file_inspect or a "
    "specialized parser/preprocessing tool instead."
)
_SNIFF_BYTES = 8192
_MAX_NUL_RATIO = 0.01
_MAX_CONTROL_RATIO = 0.20
_MAGIC_HEADERS = (
    (b"PK\x03\x04", "zip/Office archive"),
    (b"PK\x05\x06", "zip archive"),
    (b"PK\x07\x08", "zip archive"),
    (b"%PDF", "PDF"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"Rar!\x1a\x07", "RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "7z archive"),
)


class FileReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return (
            "Read plain text files from the user's workspace. Do not use this "
            "for Office, PDF, images, archives, or binary files; use "
            "file_inspect or a specialized parser instead."
        )

    @property
    def metadata(self) -> "ToolMetadata":
        from tools.base import ToolMetadata, RESULT_SIZE_UNLIMITED
        return ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="medium",
            access_scope="session_only",
            mutates_session_state=False,
            max_result_size_chars=RESULT_SIZE_UNLIMITED,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a plain text file within the workspace.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start reading from this line number (1-based). Default: 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return. Default: all.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        path: str = kwargs["path"]
        offset: int = kwargs.get("offset") or 1
        limit: Optional[int] = kwargs.get("limit") or None

        if is_internal_path(path):
            return {"output": "Access denied: path points to internal system directory", "is_error": True}

        try:
            abs_path = validate_path(ctx.workspace_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        text_check = await _validate_plain_text_file(abs_path)
        if text_check is not None:
            return {"output": text_check, "is_error": True}

        try:
            async with aiofiles.open(abs_path, mode="r", encoding="utf-8") as f:
                lines = await f.readlines()
        except FileNotFoundError:
            return {"output": f"File not found: {path}", "is_error": True}
        except IsADirectoryError:
            return {"output": f"Path is a directory: {path}", "is_error": True}
        except Exception as e:
            return {"output": str(e), "is_error": True}

        # Apply offset (1-based) and limit
        start = max(0, offset - 1)
        end = start + limit if limit else len(lines)
        selected = lines[start:end]

        return {"output": "".join(selected), "is_error": False}


async def _validate_plain_text_file(abs_path: str) -> str | None:
    ext = Path(abs_path).suffix.lower()
    if ext in _STRUCTURED_OR_BINARY_EXTENSIONS:
        return _TEXT_ERROR

    try:
        if os.path.isdir(abs_path):
            return None
        async with aiofiles.open(abs_path, mode="rb") as f:
            sample = await f.read(_SNIFF_BYTES)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return str(exc)

    if not sample:
        return None
    for magic, _label in _MAGIC_HEADERS:
        if sample.startswith(magic):
            return _TEXT_ERROR
    if sample.count(b"\x00") / len(sample) > _MAX_NUL_RATIO:
        return _TEXT_ERROR
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        return _TEXT_ERROR
    if _control_char_ratio(decoded) > _MAX_CONTROL_RATIO:
        return _TEXT_ERROR
    return None


def _control_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    allowed = {"\n", "\r", "\t", "\f", "\b"}
    control = sum(1 for char in text if ord(char) < 32 and char not in allowed)
    return control / len(text)
