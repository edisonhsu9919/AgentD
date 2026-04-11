import os
from typing import Any

import aiofiles

from tools.base import BaseTool, ToolContext
from workspace.manager import is_internal_path, validate_path


class FileWriteTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "Write or create a file in the user's workspace."

    @property
    def metadata(self) -> "ToolMetadata":
        from tools.base import ToolMetadata
        return ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=False,
            is_concurrency_safe=False,
            can_run_in_background=False,
            result_compressibility="low",
            access_scope="session_only",
            mutates_session_state=False,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within the workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        path: str = kwargs["path"]
        content: str = kwargs["content"]

        if is_internal_path(path):
            return {"output": "Access denied: path points to internal system directory", "is_error": True}

        try:
            abs_path = validate_path(ctx.workspace_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        try:
            # Auto-create parent directories
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            async with aiofiles.open(abs_path, mode="w", encoding="utf-8") as f:
                await f.write(content)
        except Exception as e:
            return {"output": str(e), "is_error": True}

        return {"output": f"Written {len(content)} bytes to {path}", "is_error": False}
