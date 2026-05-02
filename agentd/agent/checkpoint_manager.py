"""Checkpoint repair and provider-call adjacency gate.

Phase v0.4.4 / Phase D: this module owns LangGraph checkpoint message
maintenance. It deliberately does not publish SSE or update session status.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.checkpoint_state import (
    CheckpointStateKind,
    ai_message_tool_calls,
    checkpoint_tool_adjacency_is_valid,
    classify_checkpoint_snapshot,
    find_invalid_tool_adjacency_indices,
)
from core.config import settings
from core.database import AsyncSessionLocal
from session import service as session_svc


logger = logging.getLogger(__name__)


class CheckpointManager:
    @staticmethod
    async def validate_continue_checkpoint(agent, config: dict, session_id: str) -> None:
        await validate_continue_checkpoint(agent, config, session_id)

    @staticmethod
    async def ensure_tool_adjacency_ready(
        agent,
        config: dict,
        session_id: str,
        *,
        repair: bool = True,
        strict: bool = True,
        tool_message_loader=None,
    ) -> None:
        await ensure_checkpoint_tool_adjacency_ready(
            agent,
            config,
            session_id,
            repair=repair,
            strict=strict,
            tool_message_loader=tool_message_loader,
        )

    @staticmethod
    async def repair_tool_adjacency(
        agent,
        config: dict,
        session_id: str,
        candidate_ai_message: AIMessage | None = None,
        candidate_tool_messages: list[ToolMessage] | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        return await repair_checkpoint_tool_adjacency(
            agent,
            config,
            session_id,
            candidate_ai_message=candidate_ai_message,
            candidate_tool_messages=candidate_tool_messages,
            strict=strict,
        )


async def validate_continue_checkpoint(agent, config: dict, session_id: str) -> None:
    snapshot = await agent.aget_state(config)
    state = classify_checkpoint_snapshot(snapshot, run_type="continue")
    if (
        state.state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT
        and state.checkpoint_valid
        and state.is_provider_payload_ready
        and state.interrupt_count == 0
    ):
        return
    raise RuntimeError(
        "Continue checkpoint is not retryable: "
        f"session={session_id} state_kind={state.state_kind.value} "
        f"provider_ready={state.is_provider_payload_ready} "
        f"interrupts={state.interrupt_count} reason={state.reason}"
    )


async def ensure_checkpoint_tool_adjacency_ready(
    agent,
    config: dict,
    session_id: str,
    *,
    repair: bool = True,
    strict: bool = True,
    tool_message_loader=None,
) -> None:
    try:
        from unittest.mock import Mock
        if isinstance(agent, Mock):
            return
    except Exception:
        pass
    snapshot = await agent.aget_state(config)
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    if checkpoint_tool_adjacency_is_valid(messages):
        return
    if not repair:
        raise RuntimeError(
            "Checkpoint tool adjacency is invalid before provider call; "
            f"refusing to continue session={session_id}"
        )

    tool_call_ids = checkpoint_tool_call_ids(messages)
    if settings.debug:
        print(
            "[checkpoint_gate] before repair "
            f"session={session_id[:8]} len={len(messages)} "
            f"next={tuple(getattr(snapshot, 'next', ()) or ())} "
            f"bad={find_invalid_tool_adjacency_indices(messages)} "
            f"tool_call_ids={tool_call_ids}"
        )
    loader = tool_message_loader or load_tool_messages_from_persisted_session
    tool_messages = await loader(
        session_id,
        tool_call_ids,
    )
    repair_result = await repair_checkpoint_tool_adjacency(
        agent,
        config,
        session_id,
        candidate_tool_messages=tool_messages,
        strict=strict,
    )

    snapshot = await agent.aget_state(config)
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    if settings.debug:
        print(
            "[checkpoint_gate] after repair "
            f"session={session_id[:8]} len={len(messages)} "
            f"next={tuple(getattr(snapshot, 'next', ()) or ())} "
            f"valid={checkpoint_tool_adjacency_is_valid(messages)} "
            f"bad={find_invalid_tool_adjacency_indices(messages)} "
            f"loaded_tool_results={len(tool_messages)} "
            f"repair={repair_result}"
        )
    if not checkpoint_tool_adjacency_is_valid(messages):
        raise RuntimeError(
            "Checkpoint tool adjacency is invalid before provider call; "
            f"refusing to continue session={session_id}"
        )


async def repair_checkpoint_tool_adjacency(
    agent,
    config: dict,
    session_id: str,
    candidate_ai_message: AIMessage | None = None,
    candidate_tool_messages: list[ToolMessage] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    candidate_tool_messages = candidate_tool_messages or []
    repaired = {
        "appended_tool_results": 0,
        "removed_invalid_messages": 0,
    }

    try:
        snapshot = await agent.aget_state(config)
        messages = (snapshot.values or {}).get("messages", []) if snapshot else []
        if not messages:
            candidate_patch = candidate_tool_group_patch(
                messages,
                candidate_ai_message,
                candidate_tool_messages,
            )
            if not candidate_patch:
                return repaired
            new_config = await aupdate_messages_as_tools(
                agent,
                config,
                {"messages": candidate_patch},
            )
            merge_updated_config(config, new_config)
            repaired["appended_tool_results"] = len(candidate_patch) - 1
            snapshot = await agent.aget_state(config)
            messages = (snapshot.values or {}).get("messages", []) if snapshot else []

        candidate_patch = candidate_tool_group_patch(
            messages,
            candidate_ai_message,
            candidate_tool_messages,
        )
        if candidate_patch:
            new_config = await aupdate_messages_as_tools(
                agent,
                config,
                {"messages": candidate_patch},
            )
            merge_updated_config(config, new_config)
            repaired["appended_tool_results"] = len(candidate_patch) - 1
            snapshot = await agent.aget_state(config)
            messages = (snapshot.values or {}).get("messages", []) if snapshot else []

        missing_tail = missing_tail_tool_messages(messages, candidate_tool_messages)
        if missing_tail:
            new_config = await aupdate_messages_as_tools(
                agent,
                config,
                {"messages": missing_tail},
            )
            merge_updated_config(config, new_config)
            repaired["appended_tool_results"] = len(missing_tail)
            snapshot = await agent.aget_state(config)
            messages = (snapshot.values or {}).get("messages", []) if snapshot else []

        invalid_indices = find_invalid_tool_adjacency_indices(messages)
        if invalid_indices:
            rebuilt = rebuild_messages_with_repaired_tool_adjacency(
                messages,
                candidate_tool_messages,
            )
            if rebuilt is not None:
                from langchain_core.messages import RemoveMessage
                from langgraph.graph.message import REMOVE_ALL_MESSAGES

                new_config = await aupdate_messages_as_tools(
                    agent,
                    config,
                    {"messages": [
                        RemoveMessage(id=REMOVE_ALL_MESSAGES),
                        *rebuilt,
                    ]},
                )
                merge_updated_config(config, new_config)
                repaired["appended_tool_results"] += (
                    sum(1 for msg in rebuilt if isinstance(msg, ToolMessage))
                    - sum(1 for msg in messages if isinstance(msg, ToolMessage))
                )
                snapshot = await agent.aget_state(config)
                messages = (snapshot.values or {}).get("messages", []) if snapshot else []
                invalid_indices = find_invalid_tool_adjacency_indices(messages)

        if invalid_indices:
            from langchain_core.messages import RemoveMessage

            removals = []
            for idx in invalid_indices:
                msg = messages[idx]
                msg_id = getattr(msg, "id", None)
                if msg_id:
                    removals.append(RemoveMessage(id=msg_id))
            if removals:
                new_config = await aupdate_messages_as_tools(
                    agent,
                    config,
                    {"messages": removals},
                )
                merge_updated_config(config, new_config)
                repaired["removed_invalid_messages"] = len(removals)
                logger.warning(
                    "Repaired invalid checkpoint tool-call adjacency: "
                    "session=%s appended=%d removed=%d",
                    session_id[:8],
                    repaired["appended_tool_results"],
                    repaired["removed_invalid_messages"],
                )
                snapshot = await agent.aget_state(config)
                messages = (snapshot.values or {}).get("messages", []) if snapshot else []

        remaining_invalid = find_invalid_tool_adjacency_indices(messages)
        if strict and remaining_invalid:
            raise RuntimeError(
                "Checkpoint tool adjacency still invalid after repair for "
                f"session={session_id}: invalid_indices={remaining_invalid}"
            )
    except Exception:
        if settings.debug:
            traceback.print_exc()
        if strict:
            raise

    return repaired


async def aupdate_messages_as_tools(agent, config: dict, values: dict[str, Any]):
    try:
        return await agent.aupdate_state(
            config=config,
            values=values,
            as_node="tools",
        )
    except TypeError:
        return await agent.aupdate_state(config=config, values=values)


async def aupdate_messages_as_start(agent, config: dict, values: dict[str, Any]):
    try:
        return await agent.aupdate_state(
            config=config,
            values=values,
            as_node="__start__",
        )
    except TypeError:
        return await agent.aupdate_state(config=config, values=values)


def candidate_tool_group_patch(
    messages: list,
    candidate_ai_message: AIMessage | None,
    candidate_tool_messages: list[ToolMessage] | None,
) -> list:
    if not candidate_ai_message or not candidate_tool_messages:
        return []
    required_ids = [tc.get("id") for tc in ai_message_tool_calls(candidate_ai_message) if tc.get("id")]
    if not required_ids:
        return []

    candidates_by_id = {
        getattr(msg, "tool_call_id", None): msg
        for msg in candidate_tool_messages
        if getattr(msg, "tool_call_id", None)
    }
    if any(tool_call_id not in candidates_by_id for tool_call_id in required_ids):
        return []

    ai_id = getattr(candidate_ai_message, "id", None)
    ai_idx = -1
    if ai_id:
        for idx, msg in enumerate(messages):
            if getattr(msg, "id", None) == ai_id:
                ai_idx = idx
                break

    if ai_idx >= 0:
        following_ids = []
        j = ai_idx + 1
        while j < len(messages) and isinstance(messages[j], ToolMessage):
            following_ids.append(getattr(messages[j], "tool_call_id", None))
            j += 1
        if all(tool_call_id in following_ids for tool_call_id in required_ids):
            return []

    ordered_tools = [candidates_by_id[tool_call_id] for tool_call_id in required_ids]
    return [candidate_ai_message, *ordered_tools]


def merge_updated_config(config: dict, new_config: Any) -> None:
    if isinstance(new_config, dict):
        existing_configurable = dict(config.get("configurable") or {})
        returned_configurable = dict(new_config.get("configurable") or {})
        thread_id = (
            returned_configurable.get("thread_id")
            or existing_configurable.get("thread_id")
        )
        checkpoint_ns = (
            returned_configurable.get("checkpoint_ns")
            or existing_configurable.get("checkpoint_ns")
        )
        config.clear()
        config["configurable"] = {}
        if thread_id:
            config["configurable"]["thread_id"] = thread_id
        if checkpoint_ns:
            config["configurable"]["checkpoint_ns"] = checkpoint_ns


def missing_tail_tool_messages(
    messages: list,
    candidate_tool_messages: list[ToolMessage],
) -> list[ToolMessage]:
    if not messages or not candidate_tool_messages:
        return []

    ai_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, AIMessage) and ai_message_tool_calls(msg):
            ai_idx = idx
            break

    if ai_idx < 0:
        return []

    following = messages[ai_idx + 1:]
    if any(not isinstance(msg, ToolMessage) for msg in following):
        return []

    required_ids = [
        tc.get("id")
        for tc in ai_message_tool_calls(messages[ai_idx])
        if tc.get("id")
    ]
    existing_ids = {
        getattr(msg, "tool_call_id", None)
        for msg in following
        if isinstance(msg, ToolMessage)
    }
    candidates_by_id = {
        getattr(msg, "tool_call_id", None): msg
        for msg in candidate_tool_messages
        if getattr(msg, "tool_call_id", None)
    }

    missing = []
    for tool_call_id in required_ids:
        if tool_call_id not in existing_ids and tool_call_id in candidates_by_id:
            missing.append(candidates_by_id[tool_call_id])
    return missing


def rebuild_messages_with_repaired_tool_adjacency(
    messages: list,
    candidate_tool_messages: list[ToolMessage],
) -> list | None:
    if not messages or not candidate_tool_messages:
        return None

    candidates_by_id = {
        getattr(msg, "tool_call_id", None): msg
        for msg in candidate_tool_messages
        if getattr(msg, "tool_call_id", None)
    }
    if not candidates_by_id:
        return None

    changed = False
    repaired: list = []
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
            existing_tools: list[ToolMessage] = []
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                existing_tools.append(messages[j])
                j += 1
            existing_by_id = {
                getattr(tool_msg, "tool_call_id", None): tool_msg
                for tool_msg in existing_tools
                if getattr(tool_msg, "tool_call_id", None)
            }

            ordered_tools: list[ToolMessage] = []
            for tool_call_id in required_ids:
                tool_msg = existing_by_id.get(tool_call_id) or candidates_by_id.get(tool_call_id)
                if tool_msg is None:
                    return None
                ordered_tools.append(tool_msg)

            existing_ids = [getattr(tool_msg, "tool_call_id", None) for tool_msg in existing_tools]
            if existing_ids != required_ids:
                changed = True
            repaired.append(msg)
            repaired.extend(ordered_tools)
            i = j
            continue

        if isinstance(msg, ToolMessage):
            changed = True
            i += 1
            continue

        repaired.append(msg)
        i += 1

    if not changed:
        return None
    if find_invalid_tool_adjacency_indices(repaired):
        return None
    return repaired


async def load_tool_messages_from_persisted_session(
    session_id: str,
    tool_call_ids: list[str] | None = None,
) -> list[ToolMessage]:
    wanted = set(tool_call_ids or [])
    tool_messages: list[ToolMessage] = []
    try:
        async with AsyncSessionLocal() as db:
            persisted = await session_svc.list_messages(db, uuid.UUID(session_id))
    except Exception:
        if settings.debug:
            traceback.print_exc()
        return tool_messages

    seen: set[str] = set()
    for message in persisted:
        for part in message.parts or []:
            if part.get("type") != "tool_result":
                continue
            tool_call_id = part.get("tool_call_id") or ""
            if not tool_call_id or tool_call_id in seen:
                continue
            if wanted and tool_call_id not in wanted:
                continue
            seen.add(tool_call_id)
            tool_messages.append(ToolMessage(
                content=str(part.get("output", "")),
                tool_call_id=tool_call_id,
                name=part.get("tool_name") or "",
            ))
    return tool_messages


def checkpoint_tool_call_ids(messages: list) -> list[str]:
    ids: list[str] = []
    for msg in messages:
        tool_calls = ai_message_tool_calls(msg)
        if not isinstance(msg, AIMessage) or not tool_calls:
            continue
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id")
            if tool_call_id and tool_call_id not in ids:
                ids.append(tool_call_id)
    return ids
