"""v0.4.7 Phase D tool error isolation tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import ToolException

from tools.base import BaseTool, ToolContext, ToolMetadata
from tools.registry import _make_coroutine


class _ErrorTool(BaseTool):
    @property
    def name(self) -> str:
        return "error_tool"

    @property
    def description(self) -> str:
        return "returns a tool-level error"

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=False,
            result_compressibility="low",
            access_scope="session_only",
            mutates_session_state=False,
        )

    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs):
        return {"output": "invalid tool argument", "is_error": True}


@pytest.mark.asyncio
async def test_tool_error_becomes_tool_exception_not_runtime_crash(tmp_path):
    ctx = ToolContext(
        user_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        user_root=str(tmp_path),
        session_dir=str(tmp_path),
        venv_bin="",
        publish=AsyncMock(),
        workspace_dir=str(tmp_path),
        run_id=str(uuid.uuid4()),
    )

    with pytest.raises(ToolException, match="invalid tool argument"):
        await _make_coroutine(_ErrorTool(), ctx)()

    ctx.publish.assert_not_called()
