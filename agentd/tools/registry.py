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

    def resolve_tool_names(
        self, tool_profile: str | None = None, allowed_tools: set[str] | None = None,
    ) -> set[str]:
        """Resolve the concrete tool-name set for a profile."""
        all_tools = set(self._tools)
        if tool_profile is None:
            return all_tools

        if tool_profile == "child":
            inherited = all_tools - self._CHILD_FORBIDDEN
            if allowed_tools:
                inherited &= allowed_tools
            return inherited

        return all_tools

    def _filter_by_profile(
        self, tool_profile: str | None, allowed_tools: set[str] | None = None,
    ) -> dict[str, "BaseTool"]:
        """Return tools filtered by profile."""
        resolved = self.resolve_tool_names(tool_profile, allowed_tools)
        return {n: t for n, t in self._tools.items() if n in resolved}

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
            allowed_tools: Optional narrowing set applied to the child profile.
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
_MAX_IDENTICAL_TOOL_CALLS_HARD = 6
_MAX_TOOL_CALLS_PER_RUN = 64
_MAX_BLOCKED_TOOL_CALLS_PER_RUN = 12

# Per-session signature counters: {session_id: {signature: count}}
_tool_call_counters: dict[str, dict[str, int]] = {}
_tool_loop_guard_state: dict[str, dict[str, Any]] = {}


class ToolLoopCircuitBreaker(RuntimeError):
    """Raised when the runtime hard-stops a repeated blocked tool loop."""

    def __init__(
        self,
        *,
        session_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
        blocked_count: int,
        identical_call_count: int,
        reason: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.tool_name = tool_name
        self.canonical_args = canonical_args
        self.blocked_count = blocked_count
        self.identical_call_count = identical_call_count
        self.reason = reason
        self.message = message


def _new_guard_state() -> dict[str, Any]:
    return {
        "total_calls": 0,
        "blocked_calls": 0,
        "last_trigger": None,
    }


def reset_tool_call_counter(session_id: str) -> None:
    """Reset per-run tool call counters. Call at the start of each run."""
    _tool_call_counters[session_id] = {}
    _tool_loop_guard_state[session_id] = _new_guard_state()


def get_tool_loop_guard_diagnostics(session_id: str) -> dict[str, Any]:
    """Return current tool-loop diagnostics for the active run."""
    state = _tool_loop_guard_state.get(session_id, _new_guard_state())
    last_trigger = state.get("last_trigger") or {}
    return {
        "tool_loop_total_calls": state.get("total_calls", 0),
        "tool_loop_blocked_calls": state.get("blocked_calls", 0),
        "tool_loop_guard_triggered": bool(last_trigger),
        "tool_loop_guard_reason": last_trigger.get("reason", ""),
        "tool_loop_guard_tool_name": last_trigger.get("tool_name", ""),
        "tool_loop_guard_canonical_args": last_trigger.get("canonical_args"),
        "tool_loop_guard_blocked_count": last_trigger.get("blocked_count", 0),
        "tool_loop_guard_identical_call_count": last_trigger.get("identical_call_count", 0),
        "tool_loop_guard_message": last_trigger.get("message", ""),
    }


def _make_tool_signature(tool: BaseTool, kwargs: dict) -> tuple[str, dict[str, Any]]:
    """Create a normalized signature for dedup comparison."""
    import json as _json
    canonical = tool.canonicalize_args(kwargs)
    cleaned = {k: v for k, v in sorted(canonical.items()) if v is not None}
    return (
        f"{tool.name}|{_json.dumps(cleaned, sort_keys=True, ensure_ascii=False)}",
        cleaned,
    )


def _check_tool_dedup(session_id: str, tool: BaseTool, kwargs: dict) -> str | None:
    """Check if this tool call has been made too many times with identical args.

    Returns None if OK to proceed, or a warning message if limit reached.

    After the limit, returns progressively shorter messages to minimize
    token waste from repeated blocked calls.
    """
    counters = _tool_call_counters.setdefault(session_id, {})
    state = _tool_loop_guard_state.setdefault(session_id, _new_guard_state())
    state["total_calls"] += 1
    sig, canonical_args = _make_tool_signature(tool, kwargs)
    count = counters.get(sig, 0)

    if state["total_calls"] > _MAX_TOOL_CALLS_PER_RUN:
        raise _build_tool_loop_breaker(
            session_id=session_id,
            tool_name=tool.name,
            canonical_args=canonical_args,
            blocked_count=state["blocked_calls"],
            identical_call_count=count + 1,
            reason="tool_call_budget_exceeded",
        )

    if count >= _MAX_IDENTICAL_TOOL_CALLS_HARD:
        counters[sig] = count + 1
        _tool_call_counters[session_id] = counters
        state["blocked_calls"] += 1
        raise _build_tool_loop_breaker(
            session_id=session_id,
            tool_name=tool.name,
            canonical_args=canonical_args,
            blocked_count=(count - _TOOL_DEDUP_MAX + 1),
            identical_call_count=count + 1,
            reason="identical_tool_call_loop",
        )

    if count >= _TOOL_DEDUP_MAX:
        excess = count - _TOOL_DEDUP_MAX
        # First blocked call: full explanation
        if excess == 0:
            msg = (
                f"BLOCKED: {tool.name} called {_TOOL_DEDUP_MAX} times with identical parameters. "
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
        state["blocked_calls"] += 1
        if state["blocked_calls"] >= _MAX_BLOCKED_TOOL_CALLS_PER_RUN:
            raise _build_tool_loop_breaker(
                session_id=session_id,
                tool_name=tool.name,
                canonical_args=canonical_args,
                blocked_count=state["blocked_calls"],
                identical_call_count=count + 1,
                reason="blocked_tool_call_budget_exceeded",
            )
        return msg

    counters[sig] = count + 1
    _tool_call_counters[session_id] = counters
    return None


def _build_tool_loop_breaker(
    *,
    session_id: str,
    tool_name: str,
    canonical_args: dict[str, Any],
    blocked_count: int,
    identical_call_count: int,
    reason: str,
) -> ToolLoopCircuitBreaker:
    message = (
        "Tool loop circuit breaker triggered: the model kept repeating an already-blocked "
        f"{tool_name} call with the same canonical parameters. "
        "Stop calling this tool with the same arguments and summarize from the existing "
        "results or wait for further user input."
    )
    breaker = ToolLoopCircuitBreaker(
        session_id=session_id,
        tool_name=tool_name,
        canonical_args=canonical_args,
        blocked_count=blocked_count,
        identical_call_count=identical_call_count,
        reason=reason,
        message=message,
    )
    _tool_loop_guard_state[session_id] = {
        **_tool_loop_guard_state.get(session_id, _new_guard_state()),
        "last_trigger": {
            "reason": reason,
            "tool_name": tool_name,
            "canonical_args": canonical_args,
            "blocked_count": blocked_count,
            "identical_call_count": identical_call_count,
            "message": message,
        },
    }
    return breaker


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
        from tools.knowledge_routing import guard_knowledge_route, note_knowledge_tool_result

        run_key = ctx.run_id or ctx.session_id

        route_block = guard_knowledge_route(run_key, tool.name)
        if route_block:
            raise ToolException(route_block)

        # Per-run dedup guard — prevent identical tool call loops
        dedup_warning = _check_tool_dedup(ctx.session_id, tool, kwargs)
        if dedup_warning:
            # Raise as ToolException so model sees it as an error, not a success
            raise ToolException(dedup_warning)

        result = await tool.execute(ctx, **kwargs)
        note_knowledge_tool_result(run_key, tool.name, bool(result.get("is_error")))
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


async def execute_registered_tool(
    tool_name: str,
    ctx: ToolContext,
    tool_input: dict[str, Any],
) -> str:
    """Execute a registered tool through the same runtime wrapper as ToolNode."""
    tool = get_registry().get(tool_name)
    if tool is None:
        raise ToolException(f"Unknown tool: {tool_name}")
    return await _make_coroutine(tool, ctx)(**dict(tool_input or {}))


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
