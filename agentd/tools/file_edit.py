"""file_edit tool — partial file editing (Phase E).

Performs targeted find-and-replace within a file. Safer than full
file_write for small edits — preserves untouched content exactly.
"""

from typing import Any

import aiofiles

from tools.base import BaseTool, ToolContext
from workspace.manager import is_internal_path, validate_path


class FileEditTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing a specific text section. "
            "Provide the exact old text and the new text to replace it with. "
            "Safer than file_write for targeted edits."
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the workspace.",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find in the file. Must match exactly (including whitespace).",
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace old_text with.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        path: str = kwargs["path"]
        old_text: str = kwargs["old_text"]
        new_text: str = kwargs["new_text"]

        if is_internal_path(path):
            return {"output": "Access denied: path points to internal system directory", "is_error": True}

        try:
            abs_path = validate_path(ctx.session_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        # Read existing content
        try:
            async with aiofiles.open(abs_path, mode="r", encoding="utf-8") as f:
                content = await f.read()
        except FileNotFoundError:
            return {"output": f"File not found: {path}", "is_error": True}
        except Exception as e:
            return {"output": str(e), "is_error": True}

        # Find the old text
        count = content.count(old_text)
        if count == 0:
            return {
                "output": "old_text not found in file. Ensure it matches exactly (including whitespace and newlines).",
                "is_error": True,
            }
        if count > 1:
            return {
                "output": f"old_text found {count} times. Provide a more specific (longer) old_text to match exactly once.",
                "is_error": True,
            }

        # Replace
        new_content = content.replace(old_text, new_text, 1)

        try:
            async with aiofiles.open(abs_path, mode="w", encoding="utf-8") as f:
                await f.write(new_content)
        except Exception as e:
            return {"output": str(e), "is_error": True}

        return {
            "output": f"Edited {path}: replaced 1 occurrence ({len(old_text)} → {len(new_text)} chars)",
            "is_error": False,
        }
