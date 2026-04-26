"""DEPRECATED — state is now managed internally by create_agent() in runtime.py.

This file is no longer used. See §4 of AGENTD_CONTRACT.md for current state design.
"""

from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


class TokenUsage(TypedDict):
    input: int
    output: int
    total: int


class AgentState(TypedDict):
    # ── Session metadata (read-only, injected by runner.py) ──────────────
    session_id: str
    user_id: str
    workspace: str          # /workspaces/{user_id}/
    agent_id: str           # "assistant" | "plan" (legacy "build" aliases to "assistant")
    model_id: str

    # ── Message history (LangGraph managed, auto-append) ─────────────────
    messages: Annotated[list, add_messages]

    # ── Runtime state ────────────────────────────────────────────────────
    token_usage: TokenUsage
    step_count: int
    max_steps: int                    # default 50
    context_window_limit: int         # model context window size

    # ── Permission ───────────────────────────────────────────────────────
    pending_permission_id: Optional[str]

    # ── Skill context (dynamically appended to system prompt) ────────────
    loaded_skills: list[str]          # initialized to [] by runner.py

    # ── Streaming event callback (not persisted) ─────────────────────────
    event_callback: Optional[object]  # reference to EventBus.publish
