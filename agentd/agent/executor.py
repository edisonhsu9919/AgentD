"""Agent executor — pure graph execution logic.

Extracted from runner.py (Phase C). Owns:
- Streaming agent events → SSE translation
- HITL interrupt handling (policy evaluation + permission creation)
- Message persistence + finalization
- Abort boundary checks

Does NOT own scheduling, claim, or task lifecycle — that's scheduler + worker.
"""

import asyncio
import traceback
import uuid
from typing import Any, Callable, Coroutine, Optional

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from agent.runtime import build_agent
from core.config import settings
from core.database import AsyncSessionLocal
from permission import service as perm_svc
from session import service as session_svc


# Type alias for the event publish function (decoupled from event_bus singleton)
PublishFn = Callable[[str, dict], Coroutine[Any, Any, None]]

# Type alias for abort check function
AbortCheckFn = Callable[[], Coroutine[Any, Any, bool]]


async def execute_start(
    session_id: str,
    user_id: str,
    user_root: str,
    session_dir: str,
    agent_id: str,
    model_id: str,
    user_message: str,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> None:
    """Execute a 'start' run: build agent, stream, handle interrupts, finalize.

    Args:
        publish: Async function(session_id, event_dict) for SSE events.
        check_abort: Optional async function() -> bool, checked at boundaries.
    """
    agent = await build_agent(session_id, user_id, user_root, session_dir, agent_id, model_id)
    config = {"configurable": {"thread_id": session_id}}
    initial_input = {"messages": [{"role": "user", "content": user_message}]}

    await _execute_graph(
        agent, initial_input, config, session_id, session_dir, publish, check_abort,
    )


async def execute_resume(
    session_id: str,
    decisions: list[dict],
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> None:
    """Execute a 'resume' run: rebuild agent from DB, resume graph with decisions.

    Args:
        decisions: list of {"type": "approve"} or {"type": "reject", "message": "..."}
        publish: Async function for SSE events.
        check_abort: Optional abort check.
    """
    from langgraph.types import Command

    # Rebuild agent from session metadata
    async with AsyncSessionLocal() as db:
        from auth.models import User
        from workspace.manager import get_session_dir

        sid = uuid.UUID(session_id)
        session = await session_svc.get_session(db, sid)
        if not session:
            raise RuntimeError(f"Session {session_id} not found")
        user = await db.get(User, session.user_id)
        user_root = user.workspace if user else settings.workspace_root
        session_dir = get_session_dir(user_root, session_id)

    agent = await build_agent(
        session_id=session_id,
        user_id=str(session.user_id),
        user_root=user_root,
        session_dir=session_dir,
        agent_id=session.agent_id,
        model_id=session.model_id,
    )
    config = {"configurable": {"thread_id": session_id}}

    # ── Validate decisions count matches hanging tool calls ──
    snapshot = await agent.aget_state(config)
    if snapshot and snapshot.interrupts:
        interrupt_data = snapshot.interrupts[0].value
        action_requests = interrupt_data.get("action_requests", [])
        expected = len(action_requests)
        actual = len(decisions)
        if actual != expected:
            print(
                f"[executor] resume decisions mismatch: "
                f"expected={expected}, actual={actual}, session={session_id}"
            )
            # Trim or pad to match — prevents LangGraph ValueError
            if actual > expected:
                decisions = decisions[:expected]
            else:
                while len(decisions) < expected:
                    decisions.append({"type": "reject", "message": "Permission auto-denied (mismatch)"})

    resume_payload = Command(resume={"decisions": decisions})

    await _execute_graph(
        agent, resume_payload, config, session_id, session_dir, publish, check_abort,
    )


# ── Core execution loop ──────────────────────────────────────────────────


async def _execute_graph(
    agent,
    input_data: Any,
    config: dict,
    session_id: str,
    session_dir: str,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> None:
    """Stream agent, handle interrupts, finalize. Raises on error."""
    await _stream_and_translate(agent, input_data, config, session_id, publish)

    # Check abort boundary
    if check_abort and await check_abort():
        await _update_db_status(session_id, "idle")
        await publish(session_id, {"event": "status_change", "status": "idle"})
        return

    # Check for HITL interrupt
    snapshot = await agent.aget_state(config)
    if snapshot.interrupts:
        needs_manual = await _handle_interrupt(
            session_id, session_dir, snapshot, config, agent, publish, check_abort,
        )
        if needs_manual:
            return  # Waiting for user approval — worker will exit, resume enqueued later

    else:
        # No interrupt — graph completed normally
        await _finalize(agent, config, session_id, publish)


# ── SSE translation ──────────────────────────────────────────────────────


async def _stream_and_translate(
    agent, input_data: Any, config: dict, session_id: str, publish: PublishFn,
) -> None:
    """Stream agent events and translate to AgentD SSE events.

    Uses dual stream mode for token-level text streaming:
    - "messages": yields AIMessageChunk per token → text_delta (requires streaming=True)
    - "updates": yields complete node outputs → tool_start / tool_result
    """
    current_message_id: str | None = None
    think_filter = _ThinkFilter()

    async for mode, data in agent.astream(
        input_data, config=config, stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            chunk, _metadata = data
            # Token-level text delta from model node
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                if current_message_id is None:
                    current_message_id = str(uuid.uuid4())
                cleaned, reasoning_delta = think_filter.feed(chunk.content)
                if reasoning_delta:
                    await publish(session_id, {
                        "event": "reasoning_delta",
                        "message_id": current_message_id,
                        "content": reasoning_delta,
                    })
                if cleaned:
                    await publish(session_id, {
                        "event": "text_delta",
                        "message_id": current_message_id,
                        "content": cleaned,
                    })

        elif mode == "updates":
            for node_name, node_data in data.items():
                if not node_data:
                    continue

                if node_name == "model":
                    # Tool calls from complete (aggregated) model output.
                    # Text content is intentionally skipped here — already
                    # streamed token-by-token via the "messages" channel.
                    messages = node_data.get("messages", [])
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                await publish(session_id, {
                                    "event": "tool_start",
                                    "tool_call_id": tc.get("id", ""),
                                    "tool_name": tc["name"],
                                    "input": tc.get("args", {}),
                                })

                elif node_name == "tools":
                    # Flush any buffered text before switching to tool results
                    remaining = think_filter.flush()
                    if remaining and current_message_id:
                        await publish(session_id, {
                            "event": "text_delta",
                            "message_id": current_message_id,
                            "content": remaining,
                        })
                    current_message_id = None
                    think_filter = _ThinkFilter()  # reset for next model turn
                    messages = node_data.get("messages", [])
                    for msg in messages:
                        content = msg.content if hasattr(msg, "content") else str(msg)
                        is_error = _is_tool_error(msg)
                        tool_call_id = getattr(msg, "tool_call_id", "")
                        tool_name = getattr(msg, "name", "") or ""
                        await publish(session_id, {
                            "event": "tool_result",
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "output": content,
                            "is_error": is_error,
                        })

    # Flush any remaining buffered text after the stream ends
    remaining = think_filter.flush()
    if remaining and current_message_id:
        await publish(session_id, {
            "event": "text_delta",
            "message_id": current_message_id,
            "content": remaining,
        })


# ── Interrupt handling ───────────────────────────────────────────────────


def _extract_tool_call_ids(snapshot) -> list[str]:
    """Extract tool_call_ids for interrupted tools from checkpoint state."""
    from agent.runtime import _HITL_INTERRUPT_ON

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


async def _handle_interrupt(
    session_id: str,
    session_dir: str,
    snapshot,
    config: dict,
    agent,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> bool:
    """Process HITL interrupts with policy-aware evaluation.

    Returns True if manual approval is needed, False if auto-resolved.
    """
    from permission.policy import load_policy
    from permission.evaluator import evaluate

    interrupt_data = snapshot.interrupts[0].value
    action_requests = interrupt_data.get("action_requests", [])

    tool_call_ids = _extract_tool_call_ids(snapshot)
    while len(tool_call_ids) < len(action_requests):
        tool_call_ids.append("")

    # ── Policy evaluation ──
    policy = load_policy(session_dir)

    if policy.mode in ("autopilot", "fsd"):
        all_auto = True
        for action in action_requests:
            decision = evaluate(policy, action["name"], action.get("args", {}))
            if decision != "allow":
                all_auto = False
                break

        if all_auto:
            # Auto-approve: audit records then resume inline
            try:
                async with AsyncSessionLocal() as db:
                    for action, tc_id in zip(action_requests, tool_call_ids):
                        await perm_svc.create_permission_request(
                            db,
                            session_id=uuid.UUID(session_id),
                            tool_call_id=tc_id,
                            tool_name=action["name"],
                            tool_input=action.get("args", {}),
                        )
                    from sqlalchemy import update as sql_update
                    from permission.models import PermissionRequest
                    await db.execute(
                        sql_update(PermissionRequest)
                        .where(
                            PermissionRequest.session_id == uuid.UUID(session_id),
                            PermissionRequest.status == "pending",
                        )
                        .values(status="auto_approved")
                    )
                    await db.commit()
            except Exception:
                if settings.debug:
                    traceback.print_exc()

            from langgraph.types import Command
            decisions = [{"type": "approve"} for _ in action_requests]
            resume_payload = Command(resume={"decisions": decisions})

            await _stream_and_translate(agent, resume_payload, config, session_id, publish)

            # Check abort boundary after auto-resume
            if check_abort and await check_abort():
                await _update_db_status(session_id, "idle")
                await publish(session_id, {"event": "status_change", "status": "idle"})
                return False

            new_snapshot = await agent.aget_state(config)
            if new_snapshot.interrupts:
                return await _handle_interrupt(
                    session_id, session_dir, new_snapshot, config, agent, publish, check_abort,
                )

            await _finalize(agent, config, session_id, publish)
            return False

    # ── Standard ask flow ──
    permission_ids: list[str] = []

    try:
        async with AsyncSessionLocal() as db:
            for action, tc_id in zip(action_requests, tool_call_ids):
                perm_id = uuid.uuid4()
                await perm_svc.create_permission_request(
                    db,
                    session_id=uuid.UUID(session_id),
                    tool_call_id=tc_id,
                    tool_name=action["name"],
                    tool_input=action.get("args", {}),
                    permission_id=perm_id,
                )
                permission_ids.append(str(perm_id))
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()

    # Update session status to waiting
    await _update_db_status(session_id, "waiting")

    # Publish permission_ask for each action
    for perm_id, action, tc_id in zip(permission_ids, action_requests, tool_call_ids):
        await publish(session_id, {
            "event": "permission_ask",
            "permission_id": perm_id,
            "tool_call_id": tc_id,
            "tool_name": action["name"],
            "input": action.get("args", {}),
        })

    return True


# ── Finalization ─────────────────────────────────────────────────────────


async def _finalize(agent, config: dict, session_id: str, publish: PublishFn) -> None:
    """Post-completion: persist messages, update status, publish done."""
    snapshot = await agent.aget_state(config)
    full_state = snapshot.values if snapshot else {}
    messages = full_state.get("messages", [])

    if messages:
        await _persist_messages(session_id, messages)
        await _persist_loaded_skills(session_id, messages)

    token_usage = _extract_token_usage(messages)

    await _update_db_status(session_id, "idle", token_usage=token_usage)

    await publish(session_id, {"event": "status_change", "status": "idle"})
    await publish(session_id, {"event": "done", "token_usage": token_usage})

    # Auto-generate title (best-effort, non-blocking)
    asyncio.create_task(_maybe_generate_title(session_id, messages, publish))


# ── Helpers (moved from runner.py) ───────────────────────────────────────


def _is_tool_error(msg) -> bool:
    if getattr(msg, "status", "") == "error":
        return True
    additional = getattr(msg, "additional_kwargs", {})
    if additional.get("is_error"):
        return True
    return False


def _extract_token_usage(messages: list) -> dict:
    total_input = 0
    total_output = 0
    for msg in messages:
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if usage and isinstance(usage, dict):
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
    return {"input": total_input, "output": total_output, "total": total_input + total_output}


async def _persist_messages(session_id: str, messages: list) -> None:
    """Persist only NEW assistant/tool messages to the messages table."""
    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)
            existing_count = await session_svc.count_messages(db, sid)

            persistable: list = []
            for msg in messages[1:]:
                if isinstance(msg, SystemMessage):
                    continue
                persistable.append(msg)

            skip = max(existing_count - 1, 0)
            new_messages = persistable[skip:]

            for msg in new_messages:
                if isinstance(msg, AIMessage):
                    parts = []
                    if msg.content:
                        raw = msg.content
                        clean = _strip_model_tags(raw)
                        # Extract reasoning content (between <think>...</think>)
                        reasoning = _extract_reasoning(raw)
                        if reasoning:
                            parts.append({"type": "reasoning", "content": reasoning})
                        if clean:
                            parts.append({"type": "text", "content": clean})
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            parts.append({
                                "type": "tool_call",
                                "tool_call_id": tc["id"],
                                "tool_name": tc["name"],
                                "input": tc["args"],
                            })
                    if parts:
                        await session_svc.create_message(
                            db, session_id=sid, role="assistant", parts=parts,
                        )
                elif isinstance(msg, ToolMessage):
                    tool_name = getattr(msg, "name", "") or ""
                    parts = [{
                        "type": "tool_result",
                        "tool_call_id": msg.tool_call_id,
                        "tool_name": tool_name,
                        "output": msg.content,
                        "is_error": _is_tool_error(msg),
                    }]
                    await session_svc.create_message(
                        db, session_id=sid, role="tool", parts=parts,
                    )
                elif isinstance(msg, HumanMessage):
                    is_summary = "[Context Summary]" in (msg.content or "")
                    parts = [{"type": "text", "content": msg.content}]
                    await session_svc.create_message(
                        db, session_id=sid, role="user", parts=parts,
                        is_summary=is_summary,
                    )

            await db.commit()
    except Exception as e:
        if settings.debug:
            print(f"[executor] _persist_messages error: {e}")
            traceback.print_exc()


async def _persist_loaded_skills(session_id: str, messages: list) -> None:
    """Persist loaded skill entries (name + version) and update usage stats.

    Extracts skill info from ToolMessage content matching ``[Skill: <name> v<ver>]``
    (Phase F2 format) or legacy ``[Skill: <name>]`` format.
    """
    import re
    from datetime import datetime, timezone
    from sqlalchemy import update as sa_update
    from skills.models import Skill as SkillModel
    from skills import service as skill_svc

    loaded: list[dict[str, str]] = []
    seen: set[str] = set()  # "name:version" dedup key

    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.content:
            # Try F2 format: [Skill: name v1.0.0]
            match = re.match(r"^\[Skill: (.+?) v(.+?)\]", msg.content)
            if match:
                name, version = match.group(1), match.group(2)
            else:
                # Legacy format: [Skill: name]
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
            # Get existing loaded_skills to merge (avoid overwriting earlier loads)
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
                # Update usage_count / last_used_at for each new entry
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
                    # Sync user_skills usage (Phase H1)
                    if user_id:
                        from skills import user_skill_service as us_svc
                        await us_svc.increment_usage(db, user_id, entry["name"])
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


class _ThinkFilter:
    """Stateful filter that strips ``<think>...</think>`` blocks from a token stream.

    Handles tag boundaries split across multiple chunks.  Captures reasoning
    content so the caller can persist or emit it separately.

    ``feed()`` returns a ``(cleaned_text, reasoning_delta)`` tuple.
    ``reasoning_delta`` contains only the NEW reasoning captured in *this* call
    (not the cumulative total), suitable for emitting incremental SSE events.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._reasoning_parts: list[str] = []

    # ------------------------------------------------------------------

    def feed(self, text: str) -> tuple[str, str]:
        """Process a chunk.

        Returns ``(cleaned_text, reasoning_delta)`` where:
        - *cleaned_text* is the non-reasoning content to emit (may be empty).
        - *reasoning_delta* is the NEW reasoning content captured this call
          (incremental, not cumulative).
        """
        pre_len = len(self._reasoning_parts)
        self._buf += text
        out: list[str] = []

        while self._buf:
            if self._in_think:
                idx = self._buf.find(self._CLOSE)
                if idx >= 0:
                    self._reasoning_parts.append(self._buf[:idx])
                    self._buf = self._buf[idx + len(self._CLOSE):]
                    self._in_think = False
                    continue
                # Partial closing tag at the tail?
                for i in range(min(len(self._CLOSE) - 1, len(self._buf)), 0, -1):
                    if self._CLOSE.startswith(self._buf[-i:]):
                        self._reasoning_parts.append(self._buf[:-i])
                        self._buf = self._buf[-i:]
                        delta = "".join(self._reasoning_parts[pre_len:])
                        return ("".join(out), delta)
                self._reasoning_parts.append(self._buf)
                self._buf = ""
            else:
                idx = self._buf.find(self._OPEN)
                if idx >= 0:
                    out.append(self._buf[:idx])
                    self._buf = self._buf[idx + len(self._OPEN):]
                    self._in_think = True
                    continue
                # Partial opening tag at the tail?
                for i in range(min(len(self._OPEN) - 1, len(self._buf)), 0, -1):
                    if self._OPEN.startswith(self._buf[-i:]):
                        out.append(self._buf[:-i])
                        self._buf = self._buf[-i:]
                        delta = "".join(self._reasoning_parts[pre_len:])
                        return ("".join(out), delta)
                out.append(self._buf)
                self._buf = ""

        delta = "".join(self._reasoning_parts[pre_len:])
        return ("".join(out), delta)

    def flush(self) -> str:
        """Flush remaining buffer at end of stream."""
        remaining = self._buf
        self._buf = ""
        return remaining

    @property
    def reasoning(self) -> str:
        """The captured reasoning content (without tags)."""
        return "".join(self._reasoning_parts).strip()


def _strip_model_tags(text: str) -> str:
    """Remove model-specific XML-like tags that leak into output.

    Handles: <think>, <minimax:tool_call>, and any other vendor-prefixed tags.
    """
    import re
    # Remove paired blocks: <think>...</think>, <minimax:tool_call>...</minimax:tool_call>, etc.
    text = re.sub(r"<(\w[\w:_-]*)>[\s\S]*?</\1>", "", text)
    # Remove any remaining standalone model-specific tags
    text = re.sub(r"</?(?:think|minimax:\w+)(?:\s[^>]*)?>", "", text)
    return text.strip()


def _extract_reasoning(text: str) -> str:
    """Extract reasoning content from ``<think>...</think>`` blocks.

    Returns the concatenated inner text of all think blocks (without tags),
    or empty string if none found.
    """
    import re
    parts = re.findall(r"<think>([\s\S]*?)</think>", text)
    return "\n".join(p.strip() for p in parts if p.strip())


# Backward-compatible alias
_strip_think_tags = _strip_model_tags


async def _maybe_generate_title(session_id: str, messages: list, publish: PublishFn) -> None:
    from pathlib import Path
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage as LCHumanMessage, SystemMessage as LCSystemMessage

    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)
            session = await session_svc.get_session(db, sid)
            if not session or session.title != "New Session":
                return

        title_prompt_path = Path(__file__).parent / "prompts" / "hidden" / "title.md"
        if not title_prompt_path.exists():
            return
        title_system = title_prompt_path.read_text(encoding="utf-8").strip()

        summary_parts: list[str] = []
        for msg in messages[:6]:
            if isinstance(msg, HumanMessage):
                content = _strip_think_tags(msg.content[:200])
                if content:
                    summary_parts.append(f"User: {content}")
            elif isinstance(msg, AIMessage) and msg.content:
                content = _strip_think_tags(msg.content[:200])
                if content:
                    summary_parts.append(f"Assistant: {content}")
        if not summary_parts:
            return

        conversation_text = "\n".join(summary_parts)

        # Resolve LLM config from DB or env fallback (Phase I2)
        from model_config.service import resolve_active_model_config
        async with AsyncSessionLocal() as config_db:
            resolved = await resolve_active_model_config(config_db)

        llm = ChatOpenAI(
            model=session.model_id,
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            streaming=False,
            max_tokens=60,
            http_async_client=httpx.AsyncClient(trust_env=False),
        )
        result = await llm.ainvoke([
            LCSystemMessage(content=title_system),
            LCHumanMessage(content=conversation_text),
        ])

        # Strip <think> from model output too (local models may include reasoning)
        title = _strip_think_tags(result.content or "")
        title = title.strip('"').strip("'")[:50]
        if not title:
            return

        async with AsyncSessionLocal() as db:
            from sqlalchemy import update as sql_update
            from session.models import Session
            await db.execute(
                sql_update(Session).where(Session.id == sid).values(title=title)
            )
            await db.commit()

        await publish(session_id, {"event": "title_update", "title": title})

        if settings.debug:
            print(f"[executor] title generated: {title}")

    except Exception:
        if settings.debug:
            traceback.print_exc()


async def _update_db_status(
    session_id: str,
    status: str,
    token_usage: dict | None = None,
) -> None:
    """Update session status in the database."""
    try:
        async with AsyncSessionLocal() as db:
            await session_svc.update_session_status(db, uuid.UUID(session_id), status)
            if token_usage:
                await session_svc.update_token_usage(
                    db, uuid.UUID(session_id), token_usage,
                )
            await db.commit()
    except Exception:
        pass
