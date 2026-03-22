"""DEPRECATED — replaced by agent/runtime.py (create_agent builds the graph internally).

This file is no longer used. The graph is now constructed by create_agent() in runtime.py.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.nodes import call_llm, compact_context, execute_tools
from agent.state import AgentState
from core.config import settings


# ── Routing functions (§8.2) ─────────────────────────────────────────────────

def route_after_llm(state: AgentState) -> str:
    """After LLM: if tool_calls present → tools_node, else → end."""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools_node"
    return END


def route_after_tools(state: AgentState) -> str:
    """After tools: if token usage > 85% limit → compact, else → back to LLM."""
    usage = state["token_usage"]
    limit = state["context_window_limit"]
    if usage["total"] > limit * 0.85:
        return "compact_node"

    # Step limit guard
    if state["step_count"] >= state["max_steps"]:
        return END

    return "llm_node"


# ── Graph construction ───────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build and return the (uncompiled) StateGraph."""
    graph = StateGraph(AgentState)
    graph.add_node("llm_node", call_llm)
    graph.add_node("tools_node", execute_tools)
    graph.add_node("compact_node", compact_context)

    graph.set_entry_point("llm_node")
    graph.add_conditional_edges("llm_node", route_after_llm)
    graph.add_conditional_edges("tools_node", route_after_tools)
    graph.add_edge("compact_node", "llm_node")

    return graph


# ── Compiled graph with checkpointer ────────────────────────────────────────

_compiled = None
_pool: AsyncConnectionPool | None = None


async def get_compiled_graph():
    """Return the compiled graph with PostgreSQL checkpointer.

    Uses a psycopg3 AsyncConnectionPool for long-lived connections.
    The pool and checkpointer are initialized lazily on first call.
    """
    global _compiled, _pool
    if _compiled is not None:
        return _compiled

    graph = build_graph()

    # Create psycopg3 connection pool (not asyncpg — required by checkpoint-postgres)
    # autocommit=True and row_factory=dict_row are required by AsyncPostgresSaver
    _pool = AsyncConnectionPool(
        conninfo=settings.checkpoint_db_url,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
        },
    )
    await _pool.open()

    checkpointer = AsyncPostgresSaver(_pool)
    await checkpointer.setup()

    _compiled = graph.compile(checkpointer=checkpointer)
    return _compiled
