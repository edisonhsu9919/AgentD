"""Session title generation sidecar."""

from __future__ import annotations

import re
import traceback
import uuid
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages import HumanMessage as LCHumanMessage
from langchain_core.messages import SystemMessage as LCSystemMessage
from sqlalchemy import select, update as sql_update

from agent.maintenance_model import (
    invoke_maintenance_chat,
    maintenance_chat_kwargs,
    maintenance_kwargs_shape,
)
from agent.provider_reasoning import extract_reasoning_from_message, strip_reasoning_tags
from agent.run_models import AgentRun
from core.config import settings
from core.database import AsyncSessionLocal
from session import service as session_svc
from session.models import Session


DEFAULT_TITLE = "New Session"
MAX_TITLE_CHARS = 80
TITLE_MODEL_TIMEOUT_SECONDS = 12.0


@dataclass
class TitleGenerationResult:
    title: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)


async def generate_session_title(session_id: str, messages: list) -> TitleGenerationResult:
    """Generate and persist a title, using a mechanical fallback on failure."""
    sid = uuid.UUID(session_id)
    diagnostics: dict[str, Any] = {
        "maintenance_title_status": "started",
        "maintenance_title_fallback_used": False,
    }

    try:
        async with AsyncSessionLocal() as db:
            session = await session_svc.get_session(db, sid)
            if not session or session.title != DEFAULT_TITLE:
                return TitleGenerationResult(None, {
                    **diagnostics,
                    "maintenance_title_status": "skipped",
                    "maintenance_title_skip_reason": "session_missing_or_non_default_title",
                })

        conversation_text = _conversation_excerpt(messages)
        if not conversation_text:
            return TitleGenerationResult(None, {
                **diagnostics,
                "maintenance_title_status": "skipped",
                "maintenance_title_skip_reason": "empty_conversation_excerpt",
            })

        title = ""
        try:
            title_system = _load_title_prompt()
            async with AsyncSessionLocal() as db:
                result, resolved = await asyncio.wait_for(
                    invoke_maintenance_chat(
                        db,
                        purpose="title",
                        messages=[
                            LCSystemMessage(content=title_system),
                            LCHumanMessage(content=conversation_text),
                        ],
                        max_tokens=256,
                    ),
                    timeout=TITLE_MODEL_TIMEOUT_SECONDS,
            )
            diagnostics["maintenance_title_model_source"] = getattr(resolved, "source", None)
            diagnostics["maintenance_title_provider_type"] = getattr(resolved, "provider_type", None)
            diagnostics["maintenance_title_model_id"] = getattr(resolved, "model_id", None)
            diagnostics["maintenance_title_kwargs_shape"] = maintenance_kwargs_shape(
                maintenance_chat_kwargs(resolved, purpose="title", max_tokens=256),
            )
            if result is not None:
                diagnostics["maintenance_title_provider_finish_reason"] = _finish_reason(result)
                raw_content = result.content if isinstance(result.content, str) else ""
                title = sanitize_session_title(raw_content, max_chars=50)
                diagnostics["maintenance_title_content_empty"] = not bool(title)
                reasoning = extract_reasoning_from_message(result).visible_text
                diagnostics["maintenance_title_reasoning_only"] = bool(reasoning and not title)
            else:
                diagnostics["maintenance_title_content_empty"] = True
                diagnostics["maintenance_title_error"] = "maintenance_model_disabled"
        except asyncio.TimeoutError:
            diagnostics["maintenance_title_error"] = (
                f"title_model_timeout:{TITLE_MODEL_TIMEOUT_SECONDS:g}s"
            )
            diagnostics["maintenance_title_content_empty"] = True
        except Exception as exc:
            diagnostics["maintenance_title_error"] = f"{type(exc).__name__}: {exc}"
            diagnostics["maintenance_title_content_empty"] = True
            if settings.debug:
                traceback.print_exc()

        if not title:
            title = fallback_session_title(messages)
            diagnostics["maintenance_title_fallback_used"] = True

        if not title:
            diagnostics["maintenance_title_status"] = "failed"
            await _record_title_diagnostics(sid, diagnostics)
            return TitleGenerationResult(None, diagnostics)

        async with AsyncSessionLocal() as db:
            session = await session_svc.get_session(db, sid)
            if not session or session.title != DEFAULT_TITLE:
                diagnostics["maintenance_title_status"] = "skipped"
                diagnostics["maintenance_title_skip_reason"] = "title_changed_before_write"
                await _record_title_diagnostics_in_db(db, sid, diagnostics)
                await db.commit()
                return TitleGenerationResult(None, diagnostics)

            await db.execute(
                sql_update(Session)
                .where(Session.id == sid)
                .values(title=title)
            )
            diagnostics["maintenance_title_status"] = "updated"
            await _record_title_diagnostics_in_db(db, sid, diagnostics)
            await db.commit()

        return TitleGenerationResult(title, diagnostics)
    except Exception as exc:
        diagnostics["maintenance_title_status"] = "failed"
        diagnostics["maintenance_title_error"] = f"{type(exc).__name__}: {exc}"
        try:
            await _record_title_diagnostics(sid, diagnostics)
        except Exception:
            pass
        if settings.debug:
            traceback.print_exc()
        return TitleGenerationResult(None, diagnostics)


async def record_title_generation_diagnostics(
    session_id: str | uuid.UUID,
    diagnostics: dict[str, Any],
) -> None:
    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    await _record_title_diagnostics(sid, diagnostics)


def sanitize_session_title(raw: str, *, max_chars: int = MAX_TITLE_CHARS) -> str:
    text = strip_reasoning_tags(raw or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("\"'`“”‘’")
    text = re.sub(r"^(标题|title)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    text = text.strip("\"'`“”‘’")
    text = text.strip(" -_#：:，,。.!！?？")
    return text[:max_chars].strip()


def fallback_session_title(messages: list) -> str:
    for msg in messages:
        role, text = _message_role_and_text(msg)
        if role == "user":
            content = text if isinstance(text, str) else str(text or "")
            content = sanitize_session_title(content, max_chars=MAX_TITLE_CHARS)
            content = re.sub(r"(/[^\s]+)+", "", content)
            content = content.strip(" -_#：:，,。.!！?？")
            if content:
                return content[:24].strip()
    return ""


def _conversation_excerpt(messages: list) -> str:
    lines: list[str] = []
    for msg in messages:
        role, text = _message_role_and_text(msg)
        if role not in {"user", "assistant"}:
            continue
        content = sanitize_session_title(text, max_chars=200)
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
        if len(lines) >= 6:
            break
    return "\n".join(lines)


def _message_role_and_text(msg: Any) -> tuple[str | None, str]:
    role: str | None = None
    text = ""

    if isinstance(msg, HumanMessage):
        role = "user"
        text = _extract_content_text(getattr(msg, "content", ""))
    elif isinstance(msg, AIMessage):
        role = "assistant"
        text = _extract_content_text(getattr(msg, "content", ""))
    elif isinstance(msg, dict):
        role = _normalize_role(msg.get("role"))
        text = _extract_message_dict_text(msg)
    else:
        role = _normalize_role(getattr(msg, "role", None) or getattr(msg, "type", None))
        text = _extract_message_dict_text({
            "parts": getattr(msg, "parts", None),
            "content": getattr(msg, "content", None),
            "text": getattr(msg, "text", None),
        })

    return role, strip_reasoning_tags(text or "").strip()


def _normalize_role(role: Any) -> str | None:
    value = str(role or "").lower()
    if value in {"human", "user"}:
        return "user"
    if value in {"ai", "assistant"}:
        return "assistant"
    return None


def _extract_message_dict_text(data: dict[str, Any]) -> str:
    parts_text = _extract_parts_text(data.get("parts"))
    if parts_text:
        return parts_text
    return _extract_content_text(data.get("content") or data.get("text"))


def _extract_parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "text":
            continue
        text = _extract_content_text(part.get("content") or part.get("text"))
        if text:
            texts.append(text)
    return "\n".join(texts)


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type and item_type not in {"text", "input_text", "output_text"}:
                    continue
                text = _extract_content_text(item.get("text") or item.get("content"))
                if text:
                    texts.append(text)
        return "\n".join(texts)
    if isinstance(content, dict):
        return _extract_content_text(content.get("text") or content.get("content"))
    return ""


def _load_title_prompt() -> str:
    title_prompt_path = Path(__file__).parent / "prompts" / "hidden" / "title.md"
    if title_prompt_path.exists():
        return title_prompt_path.read_text(encoding="utf-8").strip()
    return "Generate a concise session title. Output only the title."


def _finish_reason(result: Any) -> str | None:
    response_metadata = getattr(result, "response_metadata", None)
    if isinstance(response_metadata, dict):
        reason = response_metadata.get("finish_reason")
        if reason:
            return str(reason)
    return None


async def _record_title_diagnostics(session_id: uuid.UUID, diagnostics: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as db:
        await _record_title_diagnostics_in_db(db, session_id, diagnostics)
        await db.commit()


async def _record_title_diagnostics_in_db(
    db,
    session_id: uuid.UUID,
    diagnostics: dict[str, Any],
) -> None:
    run = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.session_id == session_id)
            .order_by(AgentRun.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return
    existing = dict(getattr(run, "diagnostics", None) or {})
    existing.update(diagnostics)
    await db.execute(
        sql_update(AgentRun)
        .where(AgentRun.id == run.id)
        .values(diagnostics=existing)
    )
