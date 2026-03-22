"""glob tool — structured file path matching (Phase E).

Returns files matching a glob pattern within the user's workspace.
Safer than `bash find` — respects workspace boundaries automatically.
"""

import fnmatch
import os
from typing import Any

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
            "Returns a list of matching relative paths."
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
        pattern: str = kwargs["pattern"]
        path: str = kwargs.get("path") or "."

        try:
            abs_path = validate_path(ctx.session_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        if not os.path.isdir(abs_path):
            return {"output": f"Not a directory: {path}", "is_error": True}

        matches = _glob_walk(abs_path, pattern)

        if not matches:
            return {"output": "No files matched.", "is_error": False}

        # Return relative paths from the search root
        rel_matches = []
        for m in matches[:_MAX_RESULTS]:
            rel = os.path.relpath(m, ctx.session_dir)
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
