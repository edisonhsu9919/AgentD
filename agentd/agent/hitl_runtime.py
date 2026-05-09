"""HITL checkpoint inspection helpers for executor split.

Phase v0.4.4 / Phase D: keep HITL inspection separate from provider payload
validation and from executor orchestration. This module owns only small,
mostly pure helpers around LangGraph interrupt snapshots.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.checkpoint_state import (
    checkpoint_tool_adjacency_is_valid,
    tail_has_unclosed_tool_calls,
    tail_tool_call_group,
)


class HITLRuntime:
    @staticmethod
    def is_resume_input(input_data: Any) -> bool:
        return is_hitl_resume_input(input_data)

    @staticmethod
    def snapshot_is_open_interrupt(snapshot) -> bool:
        return snapshot_is_open_hitl_interrupt(snapshot)

    @staticmethod
    def snapshot_next_is_interrupt(snapshot) -> bool:
        return snapshot_next_is_hitl_interrupt(snapshot)

    @staticmethod
    def snapshot_interrupt_already_resolved(snapshot) -> bool:
        return snapshot_interrupt_already_resolved(snapshot)

    @staticmethod
    def synthetic_interrupt_snapshot(snapshot):
        return synthetic_interrupt_snapshot(snapshot)

    @staticmethod
    def extract_unclosed_action_requests(snapshot) -> tuple[list[dict[str, Any]], list[str]]:
        return extract_unclosed_hitl_action_requests(snapshot)

    @staticmethod
    def extract_tool_call_ids(snapshot) -> list[str]:
        return extract_tool_call_ids(snapshot)

    @staticmethod
    def interrupt_batch_key(action_requests: list[dict], tool_call_ids: list[str]) -> tuple[str, ...]:
        return interrupt_batch_key(action_requests, tool_call_ids)


def is_hitl_resume_input(input_data: Any) -> bool:
    return bool(getattr(input_data, "resume", None))


def snapshot_is_open_hitl_interrupt(snapshot) -> bool:
    if not snapshot:
        return False
    if not (getattr(snapshot, "interrupts", None) or snapshot_next_is_hitl_interrupt(snapshot)):
        return False

    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])
    group = tail_tool_call_group(messages)
    if not group or not group["missing_ids"]:
        return False

    interrupt_ids = set(extract_tool_call_ids(snapshot))
    missing_ids = set(group["missing_ids"])
    if interrupt_ids and not missing_ids.intersection(interrupt_ids):
        return False

    from agent.runtime import _HITL_INTERRUPT_ON

    tool_calls_by_id = {
        tc.get("id"): tc
        for tc in getattr(group["ai_message"], "tool_calls", []) or []
        if tc.get("id")
    }
    hitl_candidate_ids = interrupt_ids.intersection(missing_ids) if interrupt_ids else missing_ids
    if not hitl_candidate_ids:
        return False
    for tool_call_id in hitl_candidate_ids:
        tool_call = tool_calls_by_id.get(tool_call_id) or {}
        if tool_call.get("name") not in _HITL_INTERRUPT_ON:
            return False

    return True


def snapshot_next_is_hitl_interrupt(snapshot) -> bool:
    next_nodes = getattr(snapshot, "next", None) or ()
    return any("HumanInTheLoopMiddleware.after_model" in str(node) for node in next_nodes)


def snapshot_interrupt_already_resolved(snapshot) -> bool:
    if not getattr(snapshot, "interrupts", None):
        return False
    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])
    if not checkpoint_tool_adjacency_is_valid(messages):
        return False
    tool_call_ids = extract_tool_call_ids(snapshot)
    if not tool_call_ids:
        return False
    result_ids = {
        getattr(msg, "tool_call_id", None)
        for msg in messages
        if isinstance(msg, ToolMessage)
    }
    return all(tool_call_id in result_ids for tool_call_id in tool_call_ids)


def synthetic_interrupt_snapshot(snapshot):
    actions, tool_call_ids = extract_unclosed_hitl_action_requests(snapshot)
    if not actions:
        return None
    return SimpleNamespace(
        values=getattr(snapshot, "values", {}),
        interrupts=[SimpleNamespace(value={
            "action_requests": actions,
            "tool_call_ids": tool_call_ids,
        })],
        next=getattr(snapshot, "next", ()),
    )


def extract_unclosed_hitl_action_requests(snapshot) -> tuple[list[dict[str, Any]], list[str]]:
    from agent.runtime import _HITL_INTERRUPT_ON

    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])
    group = tail_tool_call_group(messages)
    if not group:
        return [], []
    missing_ids = set(group["missing_ids"])
    ai_msg = group["ai_message"]
    actions: list[dict[str, Any]] = []
    tool_call_ids: list[str] = []
    for tool_call in getattr(ai_msg, "tool_calls", []) or []:
        if tool_call.get("id") not in missing_ids:
            continue
        if tool_call.get("name") not in _HITL_INTERRUPT_ON:
            continue
        actions.append({
            "name": tool_call["name"],
            "args": tool_call.get("args", {}),
        })
        tool_call_ids.append(tool_call.get("id", ""))
    return actions, tool_call_ids


def extract_tool_call_ids(snapshot) -> list[str]:
    from agent.runtime import _HITL_INTERRUPT_ON

    if getattr(snapshot, "interrupts", None):
        interrupt_data = snapshot.interrupts[0].value
        explicit_ids = (
            interrupt_data.get("tool_call_ids")
            if isinstance(interrupt_data, dict)
            else None
        )
        if isinstance(explicit_ids, list) and explicit_ids:
            return list(explicit_ids)

    messages = (snapshot.values or {}).get("messages", [])
    last_ai_msg = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            last_ai_msg = msg
            break

    if not last_ai_msg or not getattr(last_ai_msg, "tool_calls", None):
        return []

    return [
        tc.get("id", "") or ""
        for tc in last_ai_msg.tool_calls
        if tc.get("name") in _HITL_INTERRUPT_ON
    ]


def interrupt_batch_key(action_requests: list[dict], tool_call_ids: list[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for idx, (action, tool_call_id) in enumerate(zip(action_requests, tool_call_ids)):
        if tool_call_id:
            keys.append(tool_call_id)
        else:
            keys.append(f"idx:{idx}:{action.get('name', '')}:{action.get('args', {})}")
    return tuple(keys)


def has_unclosed_tool_calls(messages: list) -> bool:
    return tail_has_unclosed_tool_calls(messages)
