from typing import Any, Optional

import aiofiles

from tools.base import BaseTool, ToolContext
from workspace.manager import is_internal_path, validate_path, validate_path_dual


class FileReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read a file's content from the user's workspace."

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
                    "description": "Relative path within the workspace.",
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
            abs_path = validate_path_dual(ctx.session_dir, ctx.parent_session_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        try:
            async with aiofiles.open(abs_path, mode="r", encoding="utf-8", errors="replace") as f:
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
