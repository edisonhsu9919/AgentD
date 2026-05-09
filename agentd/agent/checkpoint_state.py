"""Checkpoint state classification for AgentD runtime hardening.

Phase v0.4.4 / Phase A: keep this module pure. It classifies an already
captured LangGraph snapshot or message list; it does not read/write DB,
mutate checkpoint state, or decide recovery actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class CheckpointStateKind(str, Enum):
    EMPTY = "empty"
    PROVIDER_READY = "provider_ready"
    HITL_OPEN_TOOL_CALL = "hitl_open_tool_call"
    NEXT_MODEL_AFTER_TOOL_RESULT = "next_model_after_tool_result"
    INVALID_ORPHAN_TOOL_CALL = "invalid_orphan_tool_call"
    INVALID_ORPHAN_TOOL_MESSAGE = "invalid_orphan_tool_message"
    INVALID_UNKNOWN = "invalid_unknown"


@dataclass(frozen=True)
class CheckpointState:
    state_kind: CheckpointStateKind
    is_provider_payload_ready: bool
    is_recoverable: bool
    requires_human_input: bool
    message_count: int
    next_nodes: list[str] = field(default_factory=list)
    interrupt_count: int = 0
    bad_indices: list[int] = field(default_factory=list)
    open_tool_call_ids: list[str] = field(default_factory=list)
    closed_tool_call_ids: list[str] = field(default_factory=list)
    orphan_tool_call_ids: list[str] = field(default_factory=list)
    orphan_tool_message_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def checkpoint_valid(self) -> bool:
        return self.state_kind not in {
            CheckpointStateKind.INVALID_ORPHAN_TOOL_CALL,
            CheckpointStateKind.INVALID_ORPHAN_TOOL_MESSAGE,
            CheckpointStateKind.INVALID_UNKNOWN,
        }


def classify_checkpoint_snapshot(snapshot, *, run_type: str | None = None) -> CheckpointState:
    values = getattr(snapshot, "values", {}) or {}
    return classify_checkpoint(
        messages=values.get("messages", []),
        next_nodes=[str(node) for node in (getattr(snapshot, "next", None) or ())],
        interrupts=list(getattr(snapshot, "interrupts", None) or []),
        run_type=run_type,
    )


def classify_checkpoint(
    messages: list[Any] | None,
    next_nodes: list[str] | tuple[Any, ...] | None = None,
    interrupts: list[Any] | tuple[Any, ...] | None = None,
    run_type: str | None = None,
) -> CheckpointState:
    del run_type  # Reserved for Phase B policy inputs.
    messages = list(messages or [])
    next_nodes = [str(node) for node in (next_nodes or [])]
    interrupts = list(interrupts or [])

    if not messages:
        return CheckpointState(
            state_kind=CheckpointStateKind.EMPTY,
            is_provider_payload_ready=False,
            is_recoverable=False,
            requires_human_input=False,
            message_count=0,
            next_nodes=next_nodes,
            interrupt_count=len(interrupts),
            reason="empty_checkpoint",
        )

    analysis = analyze_tool_adjacency(messages)
    interrupt_ids = set(extract_interrupt_tool_call_ids(interrupts))
    missing_ids = set(analysis.orphan_tool_call_ids)
    is_hitl_next = any("HumanInTheLoopMiddleware.after_model" in node for node in next_nodes)
    hitl_open_ids = (
        [tool_call_id for tool_call_id in analysis.orphan_tool_call_ids if tool_call_id in interrupt_ids]
        if interrupt_ids
        else list(analysis.orphan_tool_call_ids)
    )
    open_hitl = bool(missing_ids) and (
        bool(interrupts) or is_hitl_next
    ) and (
        not interrupt_ids or bool(missing_ids.intersection(interrupt_ids))
    )

    if open_hitl:
        return CheckpointState(
            state_kind=CheckpointStateKind.HITL_OPEN_TOOL_CALL,
            is_provider_payload_ready=False,
            is_recoverable=True,
            requires_human_input=True,
            message_count=len(messages),
            next_nodes=next_nodes,
            interrupt_count=len(interrupts),
            bad_indices=[],
            open_tool_call_ids=hitl_open_ids,
            closed_tool_call_ids=analysis.closed_tool_call_ids,
            orphan_tool_call_ids=analysis.orphan_tool_call_ids,
            orphan_tool_message_ids=[],
            reason="open_tool_call_matches_hitl_interrupt",
        )

    if analysis.orphan_tool_call_ids:
        return CheckpointState(
            state_kind=CheckpointStateKind.INVALID_ORPHAN_TOOL_CALL,
            is_provider_payload_ready=False,
            is_recoverable=False,
            requires_human_input=False,
            message_count=len(messages),
            next_nodes=next_nodes,
            interrupt_count=len(interrupts),
            bad_indices=analysis.bad_indices,
            open_tool_call_ids=analysis.orphan_tool_call_ids,
            closed_tool_call_ids=analysis.closed_tool_call_ids,
            orphan_tool_call_ids=analysis.orphan_tool_call_ids,
            orphan_tool_message_ids=analysis.orphan_tool_message_ids,
            reason="assistant_tool_call_missing_tool_result",
        )

    if analysis.orphan_tool_message_ids:
        return CheckpointState(
            state_kind=CheckpointStateKind.INVALID_ORPHAN_TOOL_MESSAGE,
            is_provider_payload_ready=False,
            is_recoverable=False,
            requires_human_input=False,
            message_count=len(messages),
            next_nodes=next_nodes,
            interrupt_count=len(interrupts),
            bad_indices=analysis.bad_indices,
            open_tool_call_ids=[],
            closed_tool_call_ids=analysis.closed_tool_call_ids,
            orphan_tool_call_ids=[],
            orphan_tool_message_ids=analysis.orphan_tool_message_ids,
            reason="tool_result_without_matching_assistant_tool_call",
        )

    if analysis.bad_indices:
        return CheckpointState(
            state_kind=CheckpointStateKind.INVALID_UNKNOWN,
            is_provider_payload_ready=False,
            is_recoverable=False,
            requires_human_input=False,
            message_count=len(messages),
            next_nodes=next_nodes,
            interrupt_count=len(interrupts),
            bad_indices=analysis.bad_indices,
            open_tool_call_ids=[],
            closed_tool_call_ids=analysis.closed_tool_call_ids,
            orphan_tool_call_ids=[],
            orphan_tool_message_ids=[],
            reason="invalid_checkpoint_structure",
        )

    if snapshot_next_contains_model(next_nodes) and not interrupts and isinstance(messages[-1], ToolMessage):
        return CheckpointState(
            state_kind=CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT,
            is_provider_payload_ready=True,
            is_recoverable=True,
            requires_human_input=False,
            message_count=len(messages),
            next_nodes=next_nodes,
            interrupt_count=0,
            bad_indices=[],
            open_tool_call_ids=[],
            closed_tool_call_ids=analysis.closed_tool_call_ids,
            orphan_tool_call_ids=[],
            orphan_tool_message_ids=[],
            reason="all_tool_calls_closed_next_model",
        )

    return CheckpointState(
        state_kind=CheckpointStateKind.PROVIDER_READY,
        is_provider_payload_ready=True,
        is_recoverable=False,
        requires_human_input=False,
        message_count=len(messages),
        next_nodes=next_nodes,
        interrupt_count=len(interrupts),
        bad_indices=[],
        open_tool_call_ids=[],
        closed_tool_call_ids=analysis.closed_tool_call_ids,
        orphan_tool_call_ids=[],
        orphan_tool_message_ids=[],
        reason="checkpoint_provider_ready",
    )


@dataclass(frozen=True)
class ToolAdjacencyAnalysis:
    bad_indices: list[int]
    closed_tool_call_ids: list[str]
    orphan_tool_call_ids: list[str]
    orphan_tool_message_ids: list[str]


def analyze_tool_adjacency(messages: list[Any]) -> ToolAdjacencyAnalysis:
    bad_indices: set[int] = set()
    closed_tool_call_ids: list[str] = []
    orphan_tool_call_ids: list[str] = []
    orphan_tool_message_ids: list[str] = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        tool_calls = ai_message_tool_calls(msg)
        if isinstance(msg, AIMessage) and tool_calls:
            required_ids = [
                tc.get("id")
                for tc in tool_calls
                if tc.get("id")
            ]
            j = i + 1
            tool_indices: list[int] = []
            tool_ids: list[str | None] = []
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                tool_indices.append(j)
                tool_id = getattr(messages[j], "tool_call_id", None)
                tool_ids.append(tool_id)
                j += 1

            missing_ids = [
                tool_call_id
                for tool_call_id in required_ids
                if tool_call_id not in tool_ids
            ]
            extra_ids = [
                tool_call_id
                for tool_call_id in tool_ids
                if tool_call_id not in required_ids
            ]
            matched_ids = [
                tool_call_id
                for tool_call_id in required_ids
                if tool_call_id in tool_ids
            ]

            _append_unique(closed_tool_call_ids, matched_ids)
            _append_unique(orphan_tool_call_ids, missing_ids)
            _append_unique(orphan_tool_message_ids, [tid for tid in extra_ids if tid])

            if missing_ids or extra_ids or len(tool_ids) < len(required_ids):
                bad_indices.add(i)
                bad_indices.update(tool_indices)
            i = max(j, i + 1)
            continue

        if isinstance(msg, ToolMessage):
            bad_indices.add(i)
            tool_id = getattr(msg, "tool_call_id", None)
            if tool_id:
                _append_unique(orphan_tool_message_ids, [tool_id])

        i += 1

    return ToolAdjacencyAnalysis(
        bad_indices=sorted(bad_indices),
        closed_tool_call_ids=closed_tool_call_ids,
        orphan_tool_call_ids=orphan_tool_call_ids,
        orphan_tool_message_ids=orphan_tool_message_ids,
    )


def checkpoint_tool_adjacency_is_valid(messages: list[Any]) -> bool:
    analysis = analyze_tool_adjacency(messages)
    return not analysis.bad_indices


def find_invalid_tool_adjacency_indices(messages: list[Any]) -> list[int]:
    return analyze_tool_adjacency(messages).bad_indices


def tail_tool_call_group(messages: list[Any]) -> dict[str, Any] | None:
    if not messages:
        return None
    ai_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], AIMessage) and ai_message_tool_calls(messages[idx]):
            ai_idx = idx
            break
    if ai_idx < 0:
        return None

    ai_msg = messages[ai_idx]
    required_ids = [
        tc.get("id")
        for tc in ai_message_tool_calls(ai_msg)
        if tc.get("id")
    ]
    following = []
    for msg in messages[ai_idx + 1:]:
        if not isinstance(msg, ToolMessage):
            break
        following.append(msg)
    tool_ids = [
        getattr(msg, "tool_call_id", None)
        for msg in following
    ]
    missing_ids = [tool_call_id for tool_call_id in required_ids if tool_call_id not in tool_ids]
    return {"ai_message": ai_msg, "missing_ids": missing_ids, "tool_ids": tool_ids}


def tail_has_unclosed_tool_calls(messages: list[Any]) -> bool:
    group = tail_tool_call_group(messages)
    return bool(group and group["missing_ids"])


def ai_message_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Return tool calls from both LangChain and provider-continuation fields."""
    if not isinstance(message, AIMessage):
        return []
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        return list(calls)

    invalid_calls = getattr(message, "invalid_tool_calls", None) or []
    if invalid_calls:
        return [
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "args": {},
            }
            for call in invalid_calls
            if isinstance(call, dict)
        ]

    raw_calls = (getattr(message, "additional_kwargs", None) or {}).get("tool_calls") or []
    normalized: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        args = function.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                args = {}
        normalized.append({
            "id": call.get("id"),
            "name": call.get("name") or function.get("name"),
            "args": args if isinstance(args, dict) else {},
        })
    return normalized


def snapshot_next_contains_model(snapshot_or_nodes) -> bool:
    nodes = (
        getattr(snapshot_or_nodes, "next", None)
        if not isinstance(snapshot_or_nodes, (list, tuple))
        else snapshot_or_nodes
    ) or ()
    for node in nodes:
        node_name = str(node)
        if node_name == "model" or node_name.endswith(".model") or node_name.endswith(":model"):
            return True
    return False


def extract_interrupt_tool_call_ids(interrupts: list[Any] | tuple[Any, ...]) -> list[str]:
    ids: list[str] = []
    for interrupt in interrupts or []:
        value = getattr(interrupt, "value", interrupt)
        if not isinstance(value, dict):
            continue
        explicit_ids = value.get("tool_call_ids")
        if isinstance(explicit_ids, list):
            _append_unique(ids, [str(tool_id) for tool_id in explicit_ids if tool_id])
    return ids


def checkpoint_composition(messages: list[Any]) -> dict[str, int]:
    human_count = sum(1 for msg in messages if isinstance(msg, HumanMessage))
    ai_count = sum(1 for msg in messages if isinstance(msg, AIMessage))
    tool_count = sum(1 for msg in messages if isinstance(msg, ToolMessage))
    system_count = sum(1 for msg in messages if isinstance(msg, SystemMessage))
    total = len(messages)
    return {
        "human": human_count,
        "ai": ai_count,
        "tool": tool_count,
        "system": system_count,
        "other": total - human_count - ai_count - tool_count - system_count,
        "total": total,
    }


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


class CheckpointStateClassifier:
    """Small namespaced facade for Phase A callers/tests."""

    classify = staticmethod(classify_checkpoint)
    classify_snapshot = staticmethod(classify_checkpoint_snapshot)
    analyze_tool_adjacency = staticmethod(analyze_tool_adjacency)
