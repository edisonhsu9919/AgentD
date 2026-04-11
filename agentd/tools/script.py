import asyncio
import os
import shutil
import tempfile
from typing import Any

from agent.runtime_env import resolve_script_execution
from tools.base import BaseTool, ToolContext

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

    @property
    def metadata(self) -> "ToolMetadata":
        from tools.base import ToolMetadata
        return ToolMetadata(
            default_permission="ask",
            is_read_only=False,
            is_destructive=False,
            is_concurrency_safe=False,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="session_only",
            mutates_session_state=False,
        )

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

            execution = resolve_script_execution(ctx, basename)
            proc = await asyncio.create_subprocess_exec(
                execution.python_bin, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=execution.workdir,
                env=execution.build_process_env(),
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
