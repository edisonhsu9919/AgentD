import asyncio
import os
import re
from typing import Any

from tools.base import BaseTool, ToolContext

_TIMEOUT = 60  # seconds
_MAX_OUTPUT = 8000  # characters

# ── Blacklist patterns (§7.3) ────────────────────────────────────────────────
_BLACKLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-rf\s+/\s*"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmount\b"),
    re.compile(r"\bumount\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # fork bomb
]

# Regex to extract path tokens from a shell command
_ABS_PATH_RE = re.compile(r"(?:^|\s)(/[^\s;|&>]+)")       # absolute: /etc/passwd
_REL_ESCAPE_RE = re.compile(r"(?:^|\s)(\.\.[^\s;|&>]*)")  # relative escape: ../foo


def _is_blacklisted(command: str) -> bool:
    for pattern in _BLACKLIST_PATTERNS:
        if pattern.search(command):
            return True
    return False


def _has_outside_paths(command: str, workspace: str) -> bool:
    """Check if the command references paths outside the workspace (§7.3).

    Catches both absolute paths (/etc/passwd) and relative escape paths (../foo).
    """
    abs_ws = os.path.realpath(workspace)

    # Check absolute paths
    for match in _ABS_PATH_RE.finditer(command):
        path = match.group(1)
        abs_path = os.path.realpath(path)
        if not abs_path.startswith(abs_ws + os.sep) and abs_path != abs_ws:
            return True

    # Check relative paths containing ".." — resolve relative to workspace (cwd)
    for match in _REL_ESCAPE_RE.finditer(command):
        path = match.group(1)
        abs_path = os.path.realpath(os.path.join(workspace, path))
        if not abs_path.startswith(abs_ws + os.sep) and abs_path != abs_ws:
            return True

    return False


class BashTool(BaseTool):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute a shell command in the user's workspace virtual environment."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        command: str = kwargs["command"]

        # Blacklist check → immediate deny, no permission flow
        if _is_blacklisted(command):
            return {"output": "permission_denied: command matches blacklist", "is_error": True}

        # Workspace path restriction (§7.3) — reject commands referencing paths outside workspace
        if _has_outside_paths(command, ctx.session_dir):
            return {"output": "permission_denied: command references paths outside workspace", "is_error": True}

        # Build environment with venv activated
        env_prefix = f'export PATH="{ctx.venv_bin}:$PATH" && '
        full_cmd = env_prefix + command

        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=ctx.session_dir,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
            output = stdout.decode(errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            return {"output": f"Command timed out after {_TIMEOUT}s", "is_error": True}
        except Exception as e:
            return {"output": str(e), "is_error": True}

        # Truncate
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n... (truncated at {_MAX_OUTPUT} chars)"

        is_error = proc.returncode != 0
        return {"output": output, "is_error": is_error}
