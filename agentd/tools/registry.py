from typing import Any, Optional

from langchain_core.tools import StructuredTool, ToolException
from pydantic import create_model

from tools.base import BaseTool, ToolContext


# ── JSON Schema → Pydantic model conversion ─────────────────────────────────

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _schema_to_pydantic(name: str, schema: dict) -> type:
    """Convert a JSON Schema dict to a Pydantic model class."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        py_type = _JSON_TYPE_MAP.get(prop_def.get("type", "string"), str)
        if prop_name in required:
            fields[prop_name] = (py_type, ...)
        else:
            fields[prop_name] = (Optional[py_type], None)
    return create_model(f"{name}_input", **fields)


class ToolRegistry:
    """Singleton-style registry that holds all available AgentD tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    @property
    def tools(self) -> dict[str, BaseTool]:
        return dict(self._tools)

    # ── Default permission mapping (§7.2) ────────────────────────────────────
    _DEFAULT_PERMISSIONS: dict[str, str] = {
        "bash": "ask",
        "file_read": "allow",
        "file_write": "ask",
        "file_edit": "ask",
        "skill": "allow",
        "list_dir": "allow",
        "glob": "allow",
        "grep": "allow",
        "planning": "allow",
        "todo_update": "allow",
    }

    def default_permission(self, tool_name: str) -> str:
        return self._DEFAULT_PERMISSIONS.get(tool_name, "ask")

    # ── LangChain integration ────────────────────────────────────────────────

    def get_langchain_tools(self, ctx: ToolContext) -> list[StructuredTool]:
        """Convert all registered tools to LangChain StructuredTools.

        Each returned tool captures the given ``ctx`` so that LangGraph's
        ``ToolNode`` (or our custom ``execute_tools``) can invoke them with
        just the LLM-provided arguments.
        """
        lc_tools = []
        for tool in self._tools.values():
            pydantic_schema = _schema_to_pydantic(tool.name, tool.schema())
            lc_tools.append(
                StructuredTool(
                    name=tool.name,
                    description=tool.description,
                    args_schema=pydantic_schema,
                    func=None,  # sync not used
                    coroutine=_make_coroutine(tool, ctx),
                    handle_tool_error=True,
                )
            )
        return lc_tools


def _make_coroutine(tool: BaseTool, ctx: ToolContext):
    """Create an async callable that forwards kwargs to tool.execute.

    Returns a plain string on success; raises ToolException on error so
    that LangChain's ToolNode sends the error back to the LLM as a
    ToolMessage instead of crashing the graph.
    """

    async def _run(**kwargs: Any) -> str:
        result = await tool.execute(ctx, **kwargs)
        output = str(result.get("output", ""))
        if result.get("is_error"):
            raise ToolException(output)
        return output

    return _run


# ── Module-level singleton ───────────────────────────────────────────────────

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the global ToolRegistry, creating it on first call."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_defaults(_registry)
    return _registry


def _register_defaults(registry: ToolRegistry) -> None:
    """Register all built-in tools."""
    from tools.bash import BashTool
    from tools.file_edit import FileEditTool
    from tools.file_read import FileReadTool
    from tools.file_write import FileWriteTool
    from tools.glob import GlobTool
    from tools.grep import GrepTool
    from tools.list_dir import ListDirTool
    from tools.planning import PlanningTool
    from tools.skill import SkillTool
    from tools.todo_update import TodoUpdateTool

    registry.register(BashTool())
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(SkillTool())
    registry.register(ListDirTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(PlanningTool())
    registry.register(TodoUpdateTool())
