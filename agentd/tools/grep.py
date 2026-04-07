"""grep tool — structured text search (Phase E).

Searches file contents within the workspace for a regex pattern.
Safer and more structured than `bash grep`.
"""

import os
import re
from typing import Any

from tools.base import BaseTool, ToolContext
from workspace.manager import validate_path, validate_path_dual

_MAX_RESULTS = 100
_MAX_FILE_SIZE = 1_000_000  # 1MB — skip binary / huge files

# Extensions considered searchable text
_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".md", ".txt", ".html", ".css", ".scss", ".toml", ".cfg", ".ini",
    ".sh", ".bash", ".zsh", ".env", ".sql", ".xml", ".csv", ".rs",
    ".go", ".java", ".c", ".h", ".cpp", ".hpp", ".rb", ".php",
    ".swift", ".kt", ".lua", ".r", ".jl", ".ex", ".exs", ".erl",
    ".hs", ".ml", ".vim", ".el", ".dockerfile", ".makefile",
}

# Files with no extension that are likely text
_TEXT_NAMES = {
    "Makefile", "Dockerfile", "Vagrantfile", "Gemfile", "Rakefile",
    "README", "LICENSE", "CHANGELOG", "TODO", ".gitignore", ".env",
}


class GrepTool(BaseTool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents in the workspace for a regex pattern. "
            "Returns matching lines with file paths and line numbers."
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
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Subdirectory or file to search within. Default: workspace root.",
                },
                "include": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py'). Default: all text files.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        pattern_str: str = kwargs["pattern"]
        path: str = kwargs.get("path") or "."
        include: str = kwargs.get("include") or ""

        try:
            regex = re.compile(pattern_str)
        except re.error as e:
            return {"output": f"Invalid regex: {e}", "is_error": True}

        try:
            abs_path = validate_path_dual(ctx.session_dir, ctx.parent_session_dir, path)
        except PermissionError as e:
            return {"output": str(e), "is_error": True}

        results: list[str] = []

        if os.path.isfile(abs_path):
            _search_file(abs_path, regex, ctx.session_dir, results)
        elif os.path.isdir(abs_path):
            import fnmatch
            for dirpath, dirnames, filenames in os.walk(abs_path):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for filename in sorted(filenames):
                    if include and not fnmatch.fnmatch(filename, include):
                        continue
                    if not _is_text_file(filename):
                        continue
                    full = os.path.join(dirpath, filename)
                    _search_file(full, regex, ctx.session_dir, results)
                    if len(results) >= _MAX_RESULTS:
                        break
                if len(results) >= _MAX_RESULTS:
                    break
        else:
            return {"output": f"Path not found: {path}", "is_error": True}

        if not results:
            return {"output": "No matches found.", "is_error": False}

        output = "\n".join(results[:_MAX_RESULTS])
        if len(results) > _MAX_RESULTS:
            output += f"\n... ({len(results)} total matches, showing first {_MAX_RESULTS})"

        return {"output": output, "is_error": False}


def _is_text_file(filename: str) -> bool:
    """Check if a file is likely a text file based on extension or name."""
    if filename in _TEXT_NAMES:
        return True
    _, ext = os.path.splitext(filename)
    return ext.lower() in _TEXT_EXTENSIONS


def _search_file(
    file_path: str,
    regex: re.Pattern,
    session_dir: str,
    results: list[str],
) -> None:
    """Search a single file for regex matches, appending to results."""
    try:
        size = os.path.getsize(file_path)
        if size > _MAX_FILE_SIZE:
            return
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            rel = os.path.relpath(file_path, session_dir)
            for i, line in enumerate(f, 1):
                if regex.search(line):
                    results.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(results) >= _MAX_RESULTS:
                        return
    except (OSError, UnicodeDecodeError):
        pass
