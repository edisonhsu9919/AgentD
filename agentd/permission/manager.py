"""Permission manager — evaluates whether a tool call should be allowed, asked, or denied.

Contract reference: §7.2 (tool permission defaults).
"""

from tools.registry import get_registry


def evaluate(tool_name: str) -> str:
    """Evaluate the permission policy for a tool.

    Returns:
        "allow" — execute immediately, no user prompt
        "ask"   — interrupt graph, wait for user approval
        "deny"  — reject immediately
    """
    registry = get_registry()
    return registry.default_permission(tool_name)
