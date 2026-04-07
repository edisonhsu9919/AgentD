"""list_dir tool — structured directory listing (Phase E).

Returns a tree-style listing of directories and files within the
user's workspace. Safer and more structured than `bash ls/tree`.
"""

import os
from typing import Any, Optional

from tools.base import BaseTool, ToolContext
from workspace.manager import validate_path, validate_path_dual


class ListDirTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List files and directories in the workspace. "
            "Returns a structured tree view. "
            "Use this instead of 'bash ls' for cleaner, safer output."
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
            result_compressibility="high",
            access_scope="session_only",
            mutates_session_state=False,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path within the workspace. Default: current session directory.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth to recurse. Default: 3.",
                },
            },
            "required": [],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        path: str = kwargs.get("path") or "."
        max_depth: int = kwargs.get("max_depth") or 3

        try:
            abs_path = validate_path_dual(ctx.session_dir, ctx.parent_session_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        if not os.path.isdir(abs_path):
            return {"output": f"Not a directory: {path}", "is_error": True}

        lines: list[str] = []
        _walk_tree(abs_path, "", max_depth, 0, lines)

        if not lines:
            return {"output": "(empty directory)", "is_error": False}

        return {"output": "\n".join(lines), "is_error": False}


def _walk_tree(
    dir_path: str,
    prefix: str,
    max_depth: int,
    current_depth: int,
    lines: list[str],
) -> None:
    """Recursively build a tree-style listing."""
    if current_depth >= max_depth:
        return

    try:
        entries = sorted(os.listdir(dir_path))
    except PermissionError:
        lines.append(f"{prefix}[permission denied]")
        return

    # Separate dirs and files
    dirs = []
    files = []
    for name in entries:
        if name.startswith(".") and name not in (".env", ".gitignore"):
            continue  # Skip hidden files except common config
        full = os.path.join(dir_path, name)
        if os.path.isdir(full):
            dirs.append(name)
        else:
            files.append(name)

    all_entries = [(d, True) for d in dirs] + [(f, False) for f in files]

    for i, (name, is_dir) in enumerate(all_entries):
        is_last = i == len(all_entries) - 1
        connector = "└── " if is_last else "├── "
        suffix = "/" if is_dir else ""
        lines.append(f"{prefix}{connector}{name}{suffix}")

        if is_dir:
            extension = "    " if is_last else "│   "
            _walk_tree(
                os.path.join(dir_path, name),
                prefix + extension,
                max_depth,
                current_depth + 1,
                lines,
            )
