import asyncio
import os
import shutil
import tempfile
from typing import Any

from tools.base import BaseTool, ToolContext
from skills.env import resolve_env_for_script

_TIMEOUT = 60  # seconds
_MAX_OUTPUT = 8000  # characters


class ScriptTool(BaseTool):
    """Write a temporary script and execute it in the user's venv.

    Scripts are written to ``/tmp/{session_id}/`` and cleaned up after execution.
    """

    @property
    def name(self) -> str:
        return "script"

    @property
    def description(self) -> str:
        return "Write a temporary Python script and execute it in the workspace venv."

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Script filename (e.g. 'analysis.py').",
                },
                "content": {
                    "type": "string",
                    "description": "The Python script content.",
                },
            },
            "required": ["filename", "content"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        filename: str = kwargs["filename"]
        content: str = kwargs["content"]

        # Validate filename — reject path traversal attempts
        basename = os.path.basename(filename)
        if not basename or basename != filename:
            return {"output": "Invalid filename: must be a simple filename without path separators", "is_error": True}

        # Create temp dir scoped to session
        tmp_dir = os.path.join(tempfile.gettempdir(), ctx.session_id)
        os.makedirs(tmp_dir, exist_ok=True)
        script_path = os.path.join(tmp_dir, basename)

        try:
            # Write script
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Execute with per-call env resolution (Phase M4-D)
            # M4-C materializes skill scripts as scripts/<name>, so try
            # that prefix first; bare basename as fallback.
            effective_bin = resolve_env_for_script(
                ctx.session_dir, f"scripts/{basename}", ctx.venv_bin,
            )
            if effective_bin == ctx.venv_bin:
                effective_bin = resolve_env_for_script(
                    ctx.session_dir, basename, ctx.venv_bin,
                )
            python_bin = os.path.join(effective_bin, "python")
            proc = await asyncio.create_subprocess_exec(
                python_bin, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=ctx.session_dir,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
            output = stdout.decode(errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            return {"output": f"Script timed out after {_TIMEOUT}s", "is_error": True}
        except Exception as e:
            return {"output": str(e), "is_error": True}
        finally:
            # Cleanup
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Truncate
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n... (truncated at {_MAX_OUTPUT} chars)"

        is_error = proc.returncode != 0
        return {"output": output, "is_error": is_error}
