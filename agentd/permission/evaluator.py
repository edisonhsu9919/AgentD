"""Unified permission evaluator — combines base safety, session policy, and tool defaults.

Phase B: this replaces the simple permission/manager.py as the primary evaluation entry.
The evaluation chain is:

1. Base deny / sandbox (bash blacklist, path traversal) — handled by tool layer
2. Session policy (fsd / autopilot rules) — handled here
3. Default tool permission (ask / allow) — fallback from tool registry

Contract reference: §10.1 Phase B, §7.2.
"""

from typing import Literal

from permission.policy import SessionPolicy, evaluate_tool_call
from tools.registry import get_registry


def evaluate(
    policy: SessionPolicy,
    tool_name: str,
    tool_input: dict,
) -> Literal["allow", "ask", "deny"]:
    """Evaluate whether a tool call should be allowed, asked, or denied.

    Evaluation order:
    1. Session policy check (fsd auto-allows all; autopilot checks rules)
    2. Default tool permission from registry (bash=ask, file_read=allow, etc.)

    Note: base deny/sandbox rules (bash blacklist, path validation) are enforced
    by the tool's own execute() method, not here. This evaluator decides only
    whether the HITL interrupt should fire.

    Returns:
        "allow" — execute without asking
        "ask"   — interrupt for user approval
        "deny"  — not used here (tool layer responsibility)
    """
    # Step 1: Session policy
    policy_decision = evaluate_tool_call(policy, tool_name, tool_input)
    if policy_decision == "allow":
        return "allow"

    # Step 2: Default tool permission
    registry = get_registry()
    return registry.default_permission(tool_name)
