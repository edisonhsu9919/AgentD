"""Run-local routing guard for knowledge tools."""

from __future__ import annotations


_route_state: dict[str, dict[str, int | bool]] = {}


def reset_knowledge_route_state(run_key: str) -> None:
    """Reset knowledge routing state for the current run."""
    if not run_key:
        return
    _route_state[run_key] = {
        "catalog_seen": False,
        "blocked_count": 0,
    }


def guard_knowledge_route(run_key: str, tool_name: str) -> str | None:
    """Return a guard message when search/read are attempted before catalog."""
    if not run_key:
        return None

    if tool_name not in ("knowledge_search", "knowledge_read"):
        return None

    state = _route_state.setdefault(run_key, {
        "catalog_seen": False,
        "blocked_count": 0,
    })
    if state.get("catalog_seen"):
        return None

    state["blocked_count"] = int(state.get("blocked_count", 0)) + 1
    if tool_name == "knowledge_search":
        return (
            "Please call knowledge_catalog first to confirm which knowledge "
            "documents are relevant before using knowledge_search."
        )
    return (
        "Please call knowledge_catalog first to identify candidate documents "
        "before using knowledge_read."
    )


def note_knowledge_tool_result(run_key: str, tool_name: str, is_error: bool) -> None:
    """Update run-local routing state after a knowledge tool finishes."""
    if not run_key or is_error:
        return

    state = _route_state.setdefault(run_key, {
        "catalog_seen": False,
        "blocked_count": 0,
    })
    if tool_name == "knowledge_catalog":
        state["catalog_seen"] = True
