from typing import Any, Optional

from langchain_core.tools import StructuredTool, ToolException
from pydantic import create_model

from tools.base import BaseTool, ToolContext, ToolMetadata


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

    # ── Metadata access (Phase P2) ──────────────────────────────────────────

    def default_permission(self, tool_name: str) -> str:
        """Return the default permission for a tool, read from its metadata."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return "ask"
        return tool.metadata.default_permission

    def get_tool_metadata(self, tool_name: str) -> ToolMetadata | None:
        """Return metadata for a single tool, or None if not registered."""
        tool = self._tools.get(tool_name)
        return tool.metadata if tool else None

    def list_tool_metadata(self) -> dict[str, dict]:
        """Return metadata for all registered tools as serializable dicts."""
        from dataclasses import asdict
        return {
            name: asdict(tool.metadata)
            for name, tool in self._tools.items()
        }

    # ── Tool profiles (Phase P3) ──────────────────────────────────────────

    # Tools forbidden in child profiles — prevent infinite nesting
    _CHILD_FORBIDDEN = {"launch_detached_process", "launch_subagent"}

    # Default child profile: read-only tools + knowledge retrieval
    _CHILD_DEFAULT_TOOLS = {
        "file_read", "file_inspect", "list_dir", "glob", "grep",
        "knowledge_catalog", "knowledge_search", "knowledge_read",
    }

    def _filter_by_profile(
        self, tool_profile: str | None, allowed_tools: set[str] | None = None,
    ) -> dict[str, "BaseTool"]:
        """Return tools filtered by profile."""
        if tool_profile is None:
            return dict(self._tools)

        if tool_profile == "child":
            base = set(self._CHILD_DEFAULT_TOOLS)
            if allowed_tools:
                base |= allowed_tools
            # Always exclude spawn/detach tools
            base -= self._CHILD_FORBIDDEN
            return {n: t for n, t in self._tools.items() if n in base}

        # Unknown profile → return all (safe fallback)
        return dict(self._tools)

    # ── LangChain integration ────────────────────────────────────────────────

    def get_langchain_tools(
        self, ctx: ToolContext, tool_profile: str | None = None,
        allowed_tools: set[str] | None = None,
    ) -> list[StructuredTool]:
        """Convert registered tools to LangChain StructuredTools.

        Each returned tool captures the given ``ctx`` so that LangGraph's
        ``ToolNode`` (or our custom ``execute_tools``) can invoke them with
        just the LLM-provided arguments.

        Args:
            tool_profile: None for full set, "child" for restricted child agent.
            allowed_tools: Extra tools to include in child profile beyond defaults.
        """
        tools = self._filter_by_profile(tool_profile, allowed_tools)
        lc_tools = []
        for tool in tools.values():
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


# Per-session turn-level result accumulator for MAX_RESULTS_PER_TURN_CHARS
_turn_accumulators: dict[str, int] = {}


def reset_turn_accumulator(session_id: str) -> None:
    """Reset per-turn char accumulator. Call at the start of each model turn."""
    _turn_accumulators[session_id] = 0


# ── Per-run tool dedup guard (Phase 6 subagent loop fix) ────────────────────

# Max identical (tool_name + args) calls allowed per run
_TOOL_DEDUP_MAX = 3

# Per-session signature counters: {session_id: {signature: count}}
_tool_call_counters: dict[str, dict[str, int]] = {}


def reset_tool_call_counter(session_id: str) -> None:
    """Reset per-run tool call counters. Call at the start of each run."""
    _tool_call_counters[session_id] = {}


def _make_tool_signature(tool_name: str, kwargs: dict) -> str:
    """Create a normalized signature for dedup comparison."""
    import json as _json
    # Sort keys for stable comparison, ignore None values
    cleaned = {k: v for k, v in sorted(kwargs.items()) if v is not None}
    return f"{tool_name}|{_json.dumps(cleaned, sort_keys=True, ensure_ascii=False)}"


def _check_tool_dedup(session_id: str, tool_name: str, kwargs: dict) -> str | None:
    """Check if this tool call has been made too many times with identical args.

    Returns None if OK to proceed, or a warning message if limit reached.

    After the limit, returns progressively shorter messages to minimize
    token waste from repeated blocked calls.
    """
    sig = _make_tool_signature(tool_name, kwargs)
    counters = _tool_call_counters.get(session_id, {})
    count = counters.get(sig, 0)

    if count >= _TOOL_DEDUP_MAX:
        excess = count - _TOOL_DEDUP_MAX
        # First blocked call: full explanation
        if excess == 0:
            msg = (
                f"BLOCKED: {tool_name} called {_TOOL_DEDUP_MAX} times with identical parameters. "
                f"This exact call is now disabled for this run. "
                f"You MUST either: use different parameters, use a different tool, "
                f"or stop and summarize your findings."
            )
        # Subsequent blocked calls: progressively shorter to minimize token waste
        elif excess <= 2:
            msg = f"BLOCKED: identical call disabled. Change parameters or stop."
        else:
            msg = "BLOCKED."
        # Still increment so we can track how many times model ignored the block
        counters[sig] = count + 1
        _tool_call_counters[session_id] = counters
        return msg

    counters[sig] = count + 1
    _tool_call_counters[session_id] = counters
    return None


def _make_coroutine(tool: BaseTool, ctx: ToolContext):
    """Create an async callable that forwards kwargs to tool.execute.

    Returns a plain string on success; raises ToolException on error so
    that LangChain's ToolNode sends the error back to the LLM as a
    ToolMessage instead of crashing the graph.

    Phase P4-A: applies max_result_size_chars budget control. When a
    tool result exceeds the limit, the full output is saved as an
    artifact and only a preview + reference is returned to the model.
    Also enforces MAX_RESULTS_PER_TURN_CHARS aggregate budget per turn.
    """
    from tools.base import MAX_RESULTS_PER_TURN_CHARS

    async def _run(**kwargs: Any) -> str:
        # Per-run dedup guard — prevent identical tool call loops
        dedup_warning = _check_tool_dedup(ctx.session_id, tool.name, kwargs)
        if dedup_warning:
            # Raise as ToolException so model sees it as an error, not a success
            raise ToolException(dedup_warning)

        result = await tool.execute(ctx, **kwargs)
        output = str(result.get("output", ""))
        if result.get("is_error"):
            raise ToolException(output)

        # Phase P4-A: single-tool result size budget
        max_chars = tool.metadata.max_result_size_chars
        if max_chars > 0 and len(output) > max_chars:
            output = _truncate_to_artifact(
                output, max_chars, tool.name, ctx.session_dir,
            )

        # Phase P4-A: per-turn aggregate budget
        sid = ctx.session_id
        current_total = _turn_accumulators.get(sid, 0)
        if current_total + len(output) > MAX_RESULTS_PER_TURN_CHARS:
            remaining = max(MAX_RESULTS_PER_TURN_CHARS - current_total, 1000)
            output = _truncate_to_artifact(
                output, remaining, tool.name, ctx.session_dir,
            )
        _turn_accumulators[sid] = current_total + len(output)

        return output

    return _run


def _truncate_to_artifact(
    output: str,
    max_chars: int,
    tool_name: str,
    session_dir: str,
) -> str:
    """Save full output as artifact and return preview + ref.

    Phase P4-A: when a tool result exceeds max_result_size_chars,
    the full output is persisted to .agentd/artifacts/ and the
    conversation only keeps a truncated preview with a file reference.
    """
    import os
    import uuid as _uuid

    artifact_dir = os.path.join(session_dir, ".agentd", "artifacts")
    os.makedirs(artifact_dir, exist_ok=True)

    artifact_id = f"{tool_name}_{_uuid.uuid4().hex[:8]}.txt"
    artifact_path = os.path.join(artifact_dir, artifact_id)

    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(output)

    total_chars = len(output)
    total_lines = output.count("\n") + 1

    # Preview: first portion up to ~80% of budget
    preview_chars = int(max_chars * 0.8)
    preview = output[:preview_chars]

    return (
        f"{preview}\n\n"
        f"--- [Result truncated: {total_chars:,} chars / {total_lines:,} lines. "
        f"Full output saved to: .agentd/artifacts/{artifact_id}] ---"
    )


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
    from tools.file_inspect import FileInspectTool
    from tools.todo_update import TodoUpdateTool
    from tools.launch_detached import LaunchDetachedProcessTool
    from tools.launch_subagent import LaunchSubagentTool
    from tools.knowledge_catalog import KnowledgeCatalogTool
    from tools.knowledge_search import KnowledgeSearchTool
    from tools.knowledge_read import KnowledgeReadTool

    registry.register(BashTool())
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(FileInspectTool())
    registry.register(SkillTool())
    registry.register(ListDirTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(PlanningTool())
    registry.register(TodoUpdateTool())
    registry.register(LaunchDetachedProcessTool())
    registry.register(LaunchSubagentTool())
    registry.register(KnowledgeCatalogTool())
    registry.register(KnowledgeSearchTool())
    registry.register(KnowledgeReadTool())
