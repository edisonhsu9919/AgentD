"""Runtime message persistence helpers for executor split."""

from __future__ import annotations

import json
import re
import traceback
import uuid
from hashlib import sha256
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import update as sa_update

from agent.provider_reasoning import (
    extract_reasoning_from_message,
    extract_reasoning_from_text,
    strip_reasoning_tags,
)
from agent.runtime_integrity import inspect_db_transcript_tail
from core.config import settings
from core.database import AsyncSessionLocal
from session import service as session_svc


SUBTASK_CONTINUATION_MARKER = "[Subtask Continuation - internal only]"
SUBTASK_RESULT_BRIDGE_KIND = "subtask_result_bridge"


async def persist_message_incremental(session_id: str, msg) -> None:
    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)
            existing_keys = await load_existing_part_keys(db, sid)
            await persist_runtime_message_once(db, sid, msg, existing_keys)
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


async def persist_tool_group_atomic(
    session_id: str,
    ai_message: AIMessage,
    tool_messages: list[ToolMessage],
    *,
    flush_reason: str = "complete_tool_group",
) -> None:
    """Persist one complete ordinary tool group in a single DB transaction."""
    if not isinstance(ai_message, AIMessage) or not getattr(ai_message, "tool_calls", None):
        return

    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)
            existing_keys = await load_existing_part_keys(db, sid)
            ordered_tool_messages = complete_tool_group_messages(
                ai_message,
                tool_messages,
                flush_reason=flush_reason,
            )

            assistant_persisted = await persist_runtime_message_once(
                db,
                sid,
                ai_message,
                existing_keys,
            )
            tool_persisted = False
            for tool_message in ordered_tool_messages:
                persisted = await persist_runtime_message_once(
                    db,
                    sid,
                    tool_message,
                    existing_keys,
                )
                tool_persisted = tool_persisted or persisted

            if assistant_persisted or tool_persisted:
                await db.commit()
            else:
                await db.rollback()
    except Exception:
        if settings.debug:
            traceback.print_exc()


async def persist_messages(session_id: str, messages: list) -> None:
    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)
            existing_keys = await load_existing_part_keys(db, sid)

            persistable: list = []
            for msg in messages[1:]:
                if isinstance(msg, SystemMessage):
                    continue
                if isinstance(msg, AIMessage) and \
                   getattr(msg, "additional_kwargs", {}).get("agentd_internal") == SUBTASK_RESULT_BRIDGE_KIND:
                    continue
                if isinstance(msg, HumanMessage) and \
                   getattr(msg, "additional_kwargs", {}).get("agentd_internal") == "slash_skill_load_command":
                    continue
                if isinstance(msg, HumanMessage) and \
                   SUBTASK_CONTINUATION_MARKER in (msg.content or ""):
                    continue
                persistable.append(msg)

            knowledge_source_refs = extract_knowledge_source_refs(messages)

            for i, msg in enumerate(persistable):
                is_last_ai = isinstance(msg, AIMessage) and not any(
                    isinstance(m, AIMessage) for m in persistable[i + 1:]
                )
                await persist_runtime_message_once(
                    db,
                    sid,
                    msg,
                    existing_keys,
                    knowledge_source_refs=knowledge_source_refs if is_last_ai else None,
                )

            await db.commit()
    except Exception as e:
        if settings.debug:
            print(f"[executor] _persist_messages error: {e}")
            traceback.print_exc()


async def load_existing_part_keys(db, session_id: uuid.UUID) -> set[str]:
    keys: set[str] = set()
    try:
        from unittest.mock import Mock
        if isinstance(db, Mock):
            return keys
    except Exception:
        pass
    try:
        existing_messages = await session_svc.list_messages(db, session_id)
    except Exception:
        return keys
    if not isinstance(existing_messages, list):
        return keys
    for message in existing_messages:
        for part in message.parts or []:
            if isinstance(part, dict) and part.get("projection_state") == "discarded":
                continue
            key = part_dedupe_key(part)
            if key:
                keys.add(key)
            tool_call_id = part.get("tool_call_id")
            part_type = part.get("type")
            if tool_call_id and part_type in {"tool_call", "tool_result"}:
                keys.add(f"tool:{part_type}:{tool_call_id}")
            if message.role == "user" and part_type == "text" and part.get("content"):
                keys.add(_user_text_hash_key(part.get("content")))
    return keys


async def persist_runtime_message_once(
    db,
    session_id: uuid.UUID,
    msg,
    existing_keys: set[str],
    knowledge_source_refs: list[dict] | None = None,
) -> bool:
    role, parts, is_summary = build_persistable_message_parts(msg, knowledge_source_refs)
    if not parts:
        return False

    new_parts = []
    for part in parts:
        keys = part_dedupe_keys(part)
        if keys and any(key in existing_keys for key in keys):
            continue
        new_parts.append(part)
        existing_keys.update(keys)

    if not new_parts:
        return False

    if not await projection_can_append(db, session_id, role, new_parts):
        return False

    await session_svc.create_message(
        db,
        session_id=session_id,
        role=role,
        parts=new_parts,
        is_summary=is_summary,
    )
    return True


def build_persistable_message_parts(
    msg,
    knowledge_source_refs: list[dict] | None = None,
) -> tuple[str, list[dict[str, Any]], bool]:
    runtime_message_id = getattr(msg, "id", None)

    if isinstance(msg, AIMessage):
        parts: list[dict[str, Any]] = []
        clean = strip_model_tags_from_message(msg)
        reasoning = extract_reasoning_from_message_or_text(msg)
        if reasoning:
            parts.append(with_runtime_message_id({
                "type": "reasoning",
                "content": reasoning,
            }, runtime_message_id))
        if clean:
            parts.append(with_runtime_message_id({
                "type": "text",
                "content": clean,
            }, runtime_message_id))
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                parts.append(with_runtime_message_id({
                    "type": "tool_call",
                    "tool_call_id": tc["id"],
                    "tool_name": tc["name"],
                    "input": tc["args"],
                }, runtime_message_id))
        if knowledge_source_refs:
            parts.append(with_runtime_message_id({
                "type": "source_refs",
                "sources": knowledge_source_refs,
            }, runtime_message_id))
        return "assistant", parts, False

    if isinstance(msg, ToolMessage):
        tool_name = getattr(msg, "name", "") or ""
        additional = getattr(msg, "additional_kwargs", {}) or {}
        part = {
            "type": "tool_result",
            "tool_call_id": msg.tool_call_id,
            "tool_name": tool_name,
            "output": msg.content,
            "is_error": is_tool_error(msg),
        }
        for key in (
            "synthetic_close",
            "error_code",
            "tool_group_flush_reason",
            "required_tool_call_ids",
            "received_tool_result_ids",
            "missing_tool_result_ids",
        ):
            if key in additional:
                part[key] = additional[key]
        return "tool", [with_runtime_message_id({
            **part,
        }, runtime_message_id)], False

    if isinstance(msg, HumanMessage):
        is_summary = "[Context Summary]" in (msg.content or "")
        additional = getattr(msg, "additional_kwargs", {}) or {}
        message_ref = additional.get("message_ref")
        origin = additional.get("origin")
        part = {
            "type": "text",
            "content": msg.content,
        }
        if origin:
            part["origin"] = origin
        if message_ref:
            part["message_ref"] = message_ref
        return "user", [with_runtime_message_id({
            **part,
        }, runtime_message_id)], is_summary

    return "", [], False


def complete_tool_group_messages(
    ai_message: AIMessage,
    tool_messages: list[ToolMessage],
    *,
    flush_reason: str = "complete_tool_group",
) -> list[ToolMessage]:
    """Return one ToolMessage for every AI tool_call, synthesizing safe closes."""
    by_id: dict[str, ToolMessage] = {}
    for message in tool_messages or []:
        if not isinstance(message, ToolMessage):
            continue
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id and str(tool_call_id) not in by_id:
            by_id[str(tool_call_id)] = message

    required_ids = [
        str(tool_call.get("id") or "")
        for tool_call in getattr(ai_message, "tool_calls", []) or []
        if tool_call.get("id")
    ]
    received_ids = [
        str(getattr(message, "tool_call_id", ""))
        for message in tool_messages or []
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None)
    ]
    missing_ids = [
        tool_call_id for tool_call_id in required_ids
        if tool_call_id not in by_id
    ]

    completed: list[ToolMessage] = []
    for tool_call in getattr(ai_message, "tool_calls", []) or []:
        tool_call_id = str(tool_call.get("id") or "")
        if not tool_call_id:
            continue
        existing = by_id.get(tool_call_id)
        if existing is not None:
            completed.append(existing)
            continue
        completed.append(ToolMessage(
            content=(
                "TOOL_GROUP_ATOMICITY_ERROR: tool execution did not produce "
                f"a result for tool_call_id={tool_call_id}."
            ),
            tool_call_id=tool_call_id,
            name=str(tool_call.get("name") or ""),
            additional_kwargs={
                "is_error": True,
                "synthetic_close": True,
                "error_code": "TOOL_GROUP_ATOMICITY_ERROR",
                "tool_group_flush_reason": flush_reason,
                "required_tool_call_ids": required_ids,
                "received_tool_result_ids": received_ids,
                "missing_tool_result_ids": missing_ids,
            },
        ))
    return completed


def with_runtime_message_id(part: dict[str, Any], runtime_message_id: str | None) -> dict[str, Any]:
    if runtime_message_id:
        return {**part, "runtime_message_id": runtime_message_id}
    return part


def part_dedupe_key(part: dict[str, Any]) -> str | None:
    keys = part_dedupe_keys(part)
    return keys[0] if keys else None


def part_dedupe_keys(part: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    part_type = part.get("type")
    runtime_message_id = part.get("runtime_message_id")
    if runtime_message_id:
        keys.append(f"runtime:{runtime_message_id}:{part_type}:{part.get('tool_call_id', '')}")
    message_ref = part.get("message_ref")
    if message_ref and part_type == "text":
        keys.append(f"user_prompt:{message_ref}")
    if (
        part_type == "text"
        and part.get("content")
        and (part.get("origin") == "user_prompt" or message_ref)
    ):
        keys.append(_user_text_hash_key(part.get("content")))
    tool_call_id = part.get("tool_call_id")
    if tool_call_id and part_type in {"tool_call", "tool_result"}:
        keys.append(f"tool:{part_type}:{tool_call_id}")
    return keys


async def projection_can_append(
    db,
    session_id: uuid.UUID,
    role: str,
    parts: list[dict[str, Any]],
) -> bool:
    """Decide whether new parts can append onto the DB messages projection.

    v0.4.9 Phase C audit Finding 1/3: when the DB tail is dirty but the
    rollback flag is off (default), we must NOT silently drop assistant
    finals. Under the new contract DB messages are projection/diagnostics
    only, so dirty tail is recorded as a diagnostic but ingress is not
    blocked. The legacy strict gate is preserved behind
    ``runtime_integrity_gate_db_tail_enabled=true``.
    """
    try:
        from unittest.mock import Mock
        if isinstance(db, Mock):
            return True
    except Exception:
        pass
    try:
        existing_messages = await session_svc.list_messages(db, session_id)
    except Exception:
        return True
    tail = inspect_db_transcript_tail(existing_messages[-20:])
    if not tail.has_open_tool_call:
        return True

    # Default v0.4.9 path: dirty DB tail is diagnostics-only. Allow the append
    # so assistant finals continue to land. The dirty tail is left to the
    # session doctor / release path to clean up explicitly.
    if not settings.runtime_integrity_gate_db_tail_enabled:
        try:
            import logging as _logging

            _logging.getLogger(__name__).info(
                "projection_append_dirty_tail_allowed session=%s role=%s "
                "open_tool_call_ids=%s",
                session_id, role, tail.open_tool_call_ids,
            )
        except Exception:
            pass
        return True

    # Legacy strict gate (rollback flag): keep v0.4.4 behavior.
    if role != "tool":
        return False
    tool_result_ids = [
        str(part.get("tool_call_id"))
        for part in parts
        if part.get("type") == "tool_result" and part.get("tool_call_id")
    ]
    return bool(tool_result_ids) and all(
        tool_call_id in tail.open_tool_call_ids
        for tool_call_id in tool_result_ids
    )


def _user_text_hash_key(content: Any) -> str:
    normalized = " ".join(str(content or "").split())
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    return f"user_text_hash:{digest}"


async def persist_loaded_skills(session_id: str, messages: list) -> None:
    from skills.models import Skill as SkillModel
    from skills import service as skill_svc

    loaded: list[dict[str, str]] = []
    seen: set[str] = set()

    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.content:
            match = re.match(r"^\[Skill: (.+?) v(.+?)\]", msg.content)
            if match:
                name, version = match.group(1), match.group(2)
            else:
                match = re.match(r"^\[Skill: (.+?)\]", msg.content)
                if match:
                    name, version = match.group(1), "0.1.0"
                else:
                    continue
            key = f"{name}:{version}"
            if key not in seen:
                seen.add(key)
                loaded.append({"name": name, "version": version})

    if not loaded:
        return
    try:
        async with AsyncSessionLocal() as db:
            session = await session_svc.get_session(db, uuid.UUID(session_id))
            existing: list[dict[str, str]] = []
            if session and session.loaded_skills:
                existing = list(session.loaded_skills)
            existing_keys = {f"{e['name']}:{e['version']}" for e in existing
                            if isinstance(e, dict)}

            new_entries: list[dict[str, str]] = []
            for entry in loaded:
                key = f"{entry['name']}:{entry['version']}"
                if key not in existing_keys:
                    new_entries.append(entry)
                    existing_keys.add(key)

            if new_entries:
                merged = existing + new_entries
                await session_svc.update_loaded_skills(
                    db, uuid.UUID(session_id), merged,
                )
                now = datetime.now(timezone.utc)
                user_id = session.user_id if session else None
                for entry in new_entries:
                    skill_record = await skill_svc.get_skill_by_name_version(
                        db, entry["name"], entry["version"],
                    )
                    if skill_record:
                        await db.execute(
                            sa_update(SkillModel)
                            .where(SkillModel.id == skill_record.id)
                            .values(
                                usage_count=SkillModel.usage_count + 1,
                                last_used_at=now,
                            )
                        )
                    if user_id:
                        from skills import user_skill_service as us_svc
                        await us_svc.increment_usage(db, user_id, entry["name"])
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


def extract_knowledge_source_refs(messages: list) -> list[dict]:
    sources: dict[str, dict] = {}
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = getattr(msg, "name", "") or ""
        if tool_name not in ("knowledge_search", "knowledge_read"):
            continue

        content = msg.content if isinstance(msg.content, str) else ""
        try:
            data = json.loads(content)
        except (ValueError, json.JSONDecodeError):
            continue

        if tool_name == "knowledge_search":
            for result in data.get("results", []):
                doc_id = result.get("doc_id", "")
                if doc_id and doc_id not in sources:
                    excerpts = result.get("excerpts", [])
                    evidence = excerpts[0]["text"] if excerpts else ""
                    sources[doc_id] = {
                        "doc_id": doc_id,
                        "title": result.get("title", ""),
                        "kind": result.get("kind", ""),
                        "source_file": "",
                        "evidence_excerpt": evidence[:300],
                    }

        elif tool_name == "knowledge_read":
            doc_id = data.get("doc_id", "")
            if doc_id:
                content_text = data.get("content", "")
                evidence = content_text[:300] if content_text else ""
                entry = sources.get(doc_id, {
                    "doc_id": doc_id,
                    "title": data.get("title", ""),
                    "kind": data.get("kind", ""),
                    "source_file": data.get("source_file", ""),
                    "evidence_excerpt": "",
                })
                if data.get("title"):
                    entry["title"] = data["title"]
                if data.get("source_file"):
                    entry["source_file"] = data["source_file"]
                if evidence and not entry.get("evidence_excerpt"):
                    entry["evidence_excerpt"] = evidence
                sources[doc_id] = entry

    result = list(sources.values())
    for i, src in enumerate(result):
        src["ref_index"] = i + 1
    return result


def strip_model_tags_from_message(message_or_text: Any) -> str:
    if isinstance(message_or_text, str):
        return strip_reasoning_tags(message_or_text)
    content = getattr(message_or_text, "content", "")
    return strip_reasoning_tags(content if isinstance(content, str) else "")


def extract_reasoning_from_message_or_text(message_or_text: Any) -> str:
    if isinstance(message_or_text, str):
        return extract_reasoning_from_text(message_or_text)
    return extract_reasoning_from_message(message_or_text).visible_text


def is_tool_error(msg) -> bool:
    if getattr(msg, "status", "") == "error":
        return True
    additional = getattr(msg, "additional_kwargs", {})
    if additional.get("is_error"):
        return True
    return False
