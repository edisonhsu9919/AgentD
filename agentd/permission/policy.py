"""Session-scoped permission policy — file I/O, schema, rule matching.

Phase B: session_dir/.agentd/session_policy.json stores the permission mode
and approve-always rules. The file is the single source of truth for per-session
policy; the DB (permission_requests) stores audit/recovery records only.

Contract reference: §10.1 Phase B.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Schema ──────────────────────────────────────────────────────────────────


class PolicyRule(BaseModel):
    """A single approve-always rule."""

    tool: str  # tool name: bash, file_write, script
    effect: Literal["allow"] = "allow"
    match: dict[str, Any]  # e.g. {"kind": "exact_command", "command": "ls -la"}


class SessionPolicy(BaseModel):
    """Session-scoped permission policy."""

    version: int = 1
    mode: Literal["manual", "autopilot", "fsd"] = "manual"
    rules: list[PolicyRule] = Field(default_factory=list)
    updated_at: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.updated_at:
            self.updated_at = datetime.now(timezone.utc).isoformat()


# ── File I/O ────────────────────────────────────────────────────────────────

_POLICY_SUBDIR = ".agentd"
_POLICY_FILENAME = "session_policy.json"


def _policy_path(session_dir: str) -> str:
    return os.path.join(session_dir, _POLICY_SUBDIR, _POLICY_FILENAME)


def load_policy(session_dir: str) -> SessionPolicy:
    """Load session policy from disk. Returns default (manual, no rules) if absent."""
    path = _policy_path(session_dir)
    if not os.path.isfile(path):
        return SessionPolicy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SessionPolicy.model_validate(data)
    except (json.JSONDecodeError, Exception):
        return SessionPolicy()


def save_policy(session_dir: str, policy: SessionPolicy) -> None:
    """Persist session policy to disk."""
    policy.updated_at = datetime.now(timezone.utc).isoformat()
    dir_path = os.path.join(session_dir, _POLICY_SUBDIR)
    os.makedirs(dir_path, exist_ok=True)
    path = _policy_path(session_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(policy.model_dump(), f, indent=2, ensure_ascii=False)


def delete_policy(session_dir: str) -> None:
    """Remove the policy file (resets to manual / no rules)."""
    path = _policy_path(session_dir)
    if os.path.isfile(path):
        os.remove(path)


# ── Rule matching ───────────────────────────────────────────────────────────


def match_rule(rule: PolicyRule, tool_name: str, tool_input: dict) -> bool:
    """Check if a single rule matches a tool call."""
    if rule.tool != tool_name:
        return False

    kind = rule.match.get("kind", "")

    if kind == "exact_command":
        # bash: exact command string match
        return tool_input.get("command", "").strip() == rule.match.get("command", "").strip()

    if kind == "any_path_within_session":
        # file_write: any path is allowed (path validation still enforced by tool layer)
        return True

    return False


def evaluate_tool_call(
    policy: SessionPolicy,
    tool_name: str,
    tool_input: dict,
) -> Literal["allow", "ask"]:
    """Evaluate a tool call against the session policy.

    Returns:
        "allow" — policy grants auto-approval
        "ask"   — no matching rule, fall through to normal HITL

    Note: this does NOT check base deny/sandbox rules. Those are handled
    by the tool layer (bash blacklist, path validation, etc.).
    """
    if policy.mode == "fsd":
        return "allow"

    if policy.mode == "autopilot":
        for rule in policy.rules:
            if rule.effect == "allow" and match_rule(rule, tool_name, tool_input):
                return "allow"

    # manual mode or no matching rule in autopilot
    return "ask"


def add_rule(policy: SessionPolicy, rule: PolicyRule) -> SessionPolicy:
    """Add a rule to the policy. Deduplicates exact matches.

    If adding the first rule to a manual policy, auto-promote to autopilot.
    """
    # Check for duplicate
    for existing in policy.rules:
        if existing.tool == rule.tool and existing.match == rule.match:
            return policy  # already exists

    policy.rules.append(rule)

    # Auto-promote manual → autopilot when first rule is added
    if policy.mode == "manual":
        policy.mode = "autopilot"

    return policy
