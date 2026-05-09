"""glob tool — structured file path matching (Phase E).

Returns files matching a glob pattern within the user's workspace.
Safer than `bash find` — respects workspace boundaries automatically.
"""

import fnmatch
import os
from typing import Any

from tools.arg_normalization import (
    ToolArgumentValidationError,
    normalize_string_arg,
    normalize_workspace_path_arg,
)
from tools.base import BaseTool, ToolContext
from workspace.manager import validate_path
_MAX_RESULTS = 200


class GlobTool(BaseTool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern in the workspace. "
            "Supports *, **, and ? wildcards. "
            "Returns a list of matching relative paths. "
            "Pass raw JSON strings; do not wrap path or pattern in shell quotes. "
            "For the current session directory, omit path or use '.'."
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
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g. '**/*.py', 'src/*.ts', '*.md').",
                },
                "path": {
                    "type": "string",
                    "description": "Subdirectory to search within. Default: workspace root.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        try:
            pattern = normalize_string_arg(
                kwargs.get("pattern"),
                field_name="pattern",
            )
            path = normalize_workspace_path_arg(
                kwargs.get("path"),
                workspace_dir=ctx.workspace_dir,
                optional_current_dir=True,
            )
        except ToolArgumentValidationError as e:
            return {"output": str(e), "is_error": True}

        try:
            abs_path = validate_path(ctx.workspace_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        if not os.path.isdir(abs_path):
            return {"output": f"Not a directory: {path}", "is_error": True}

        matches = _glob_walk(abs_path, pattern)

        if not matches:
            return {"output": "No files matched.", "is_error": False}

        # Return relative paths from the search root
        rel_matches = []
        display_root = os.path.realpath(ctx.workspace_dir)
        for m in matches[:_MAX_RESULTS]:
            rel = os.path.relpath(os.path.realpath(m), display_root)
            rel_matches.append(rel)

        output = "\n".join(sorted(rel_matches))
        if len(matches) > _MAX_RESULTS:
            output += f"\n... ({len(matches)} total, showing first {_MAX_RESULTS})"

        return {"output": output, "is_error": False}


def _glob_walk(root: str, pattern: str) -> list[str]:
    """Walk directory tree and match files against a glob pattern.

    Supports ** for recursive matching.
    """
    results: list[str] = []
    has_doublestar = "**" in pattern

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for filename in filenames:
            if filename.startswith("."):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)

            if has_doublestar:
                # For ** patterns, use fnmatch on the full relative path
                if fnmatch.fnmatch(rel, pattern):
                    results.append(full)
            else:
                # For non-recursive patterns, match only within the target dir level
                if fnmatch.fnmatch(rel, pattern):
                    results.append(full)

        if len(results) >= _MAX_RESULTS * 2:
            break  # Safety cap

    return results
