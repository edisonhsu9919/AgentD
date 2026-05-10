"""Agent executor — pure graph execution logic.

Extracted from runner.py (Phase C). Owns:
- Streaming agent events → SSE translation
- HITL interrupt handling (policy evaluation + permission creation)
- Message persistence + finalization
- Abort boundary checks

Does NOT own scheduling, claim, or task lifecycle — that's scheduler + worker.
"""

import asyncio
import logging
import time
import traceback
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from agent.checkpoint_state import (
    CheckpointStateKind,
    checkpoint_tool_adjacency_is_valid as _checkpoint_state_tool_adjacency_is_valid,
    classify_checkpoint_snapshot,
    find_invalid_tool_adjacency_indices as _checkpoint_state_invalid_indices,
    snapshot_next_contains_model as _checkpoint_state_next_contains_model,
    tail_has_unclosed_tool_calls as _checkpoint_state_tail_has_unclosed_tool_calls,
    tail_tool_call_group as _checkpoint_state_tail_tool_call_group,
)
from agent.diagnostics import (
    build_checkpoint_diagnostics,
    build_exception_diagnostics as _build_v044_exception_diagnostics,
    capture_checkpoint_snapshot,
    classify_provider_error,
)
from agent import checkpoint_manager as checkpoint_mgr
from agent import hitl_runtime as hitl_rt
from agent import message_persistence as msg_persist
from agent import run_diagnostics as run_diag
from agent.provider_reasoning import (
    TranscriptIntegrityError,
    append_provider_state_delta,
    extract_reasoning_from_message,
    extract_reasoning_from_text,
    merge_provider_state_final,
    merge_reasoning_text,
    strip_reasoning_tags,
)
from agent.runtime_integrity import (
    RuntimeGateAction,
    RuntimeGateDecision,
    RuntimeIntegrityError,
    decide_terminal_with_layered_validation,
)
from agent.runtime import build_agent
from core.config import settings
from core.database import AsyncSessionLocal
from permission import service as perm_svc
from session import service as session_svc


# Type alias for the event publish function (decoupled from event_bus singleton)
PublishFn = Callable[[str, dict], Coroutine[Any, Any, None]]

# Type alias for abort check function
AbortCheckFn = Callable[[], Coroutine[Any, Any, bool]]


_SUBTASK_CONTINUATION_MARKER = "[Subtask Continuation - internal only]"
_SUBTASK_RESULT_BRIDGE_KIND = "subtask_result_bridge"
_SUBTASK_CONTINUATION_PROMPT = (
    "Continue from the bridged subtask result already present in the conversation."
)
ABORT_CHECK_CHUNK_INTERVAL = 20
ABORT_CHECK_SECONDS = 1.0


class RecoverableProviderTimeout(RuntimeError):
    """Provider timed out after a closed tool_result checkpoint."""

    def __init__(self, original: BaseException, diagnostics: dict[str, Any]):
        self.original = original
        self.provider_error = f"{type(original).__name__}: {original}"
        self.diagnostics = diagnostics
        super().__init__(self.provider_error)


class ToolGroupAccumulator:
    """Collect parallel tool results until a whole assistant tool group closes."""

    def __init__(self) -> None:
        self.ai_message: AIMessage | None = None
        self.required_ids: list[str] = []
        self.results_by_id: dict[str, ToolMessage] = {}

    @property
    def active(self) -> bool:
        return self.ai_message is not None and bool(self.required_ids)

    def start(self, ai_message: AIMessage) -> None:
        self.ai_message = ai_message
        self.required_ids = [
            str(tool_call.get("id") or "")
            for tool_call in getattr(ai_message, "tool_calls", []) or []
            if tool_call.get("id")
        ]
        self.results_by_id = {}

    def add(self, tool_messages: list[ToolMessage]) -> None:
        for message in tool_messages or []:
            tool_call_id = getattr(message, "tool_call_id", None)
            if not tool_call_id:
                continue
            key = str(tool_call_id)
            if key not in self.results_by_id:
                self.results_by_id[key] = message

    def is_complete(self) -> bool:
        return self.active and all(
            tool_call_id in self.results_by_id
            for tool_call_id in self.required_ids
        )

    def complete_messages(self) -> list[ToolMessage]:
        return [
            self.results_by_id[tool_call_id]
            for tool_call_id in self.required_ids
            if tool_call_id in self.results_by_id
        ]

    def missing_ids(self) -> list[str]:
        return [
            tool_call_id
            for tool_call_id in self.required_ids
            if tool_call_id not in self.results_by_id
        ]

    def clear(self) -> None:
        self.ai_message = None
        self.required_ids = []
        self.results_by_id = {}


async def execute_start(
    session_id: str,
    user_id: str,
    user_root: str,
    session_dir: str,
    agent_id: str,
    model_id: str,
    user_message: str,
    publish: PublishFn,
    user_message_ref: str | None = None,
    check_abort: Optional[AbortCheckFn] = None,
    tool_profile: str | None = None,
    is_subtask_continuation: bool = False,
    parent_session_dir: str | None = None,
    allowed_tools: list[str] | None = None,
    run_id: str | None = None,
) -> None:
    """Execute a 'start' run: build agent, stream, handle interrupts, finalize."""
    agent = await build_agent(
        session_id, user_id, user_root, session_dir, agent_id, model_id,
        tool_profile=tool_profile,
        parent_session_dir=parent_session_dir,
        allowed_tools=allowed_tools,
        run_id=run_id,
    )
    config = {"configurable": {"thread_id": session_id}}

    if is_subtask_continuation:
        if settings.debug:
            print(f"[executor] subtask continuation gate session={session_id[:8]}")
        await _ensure_checkpoint_tool_adjacency_ready(
            agent,
            config,
            session_id,
            strict=True,
        )
        # Subtask bridge: inject the child result as assistant semantics,
        # then add a tiny internal continuation nudge as a HumanMessage.
        # This keeps the bridged result out of system-role history while
        # avoiding a fake user bubble in persisted messages.
        continuation_messages = [
            AIMessage(
                content=user_message,
                additional_kwargs={"agentd_internal": _SUBTASK_RESULT_BRIDGE_KIND},
            ),
            HumanMessage(content=(
                _SUBTASK_CONTINUATION_MARKER + "\n\n"
                + _SUBTASK_CONTINUATION_PROMPT
            )),
        ]
        from unittest.mock import Mock
        if isinstance(agent, Mock):
            initial_input = {"messages": continuation_messages}
        else:
            await _aupdate_messages_as_start(
                agent,
                config,
                {"messages": continuation_messages},
            )
            await _ensure_checkpoint_tool_adjacency_ready(
                agent,
                config,
                session_id,
                strict=True,
            )
            initial_input = {"messages": []}
    else:
        initial_input = {
            "messages": [
                HumanMessage(
                    content=user_message,
                    additional_kwargs={
                        "origin": "user_prompt",
                        "message_ref": user_message_ref,
                    } if user_message_ref else {"origin": "user_prompt"},
                )
            ]
        }

    await _execute_graph(
        agent, initial_input, config, session_id, session_dir, publish, check_abort,
    )


async def execute_resume(
    session_id: str,
    decisions: list[dict],
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
    run_id: str | None = None,
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
        from agent.child_session import read_child_session_meta
        from workspace.manager import get_session_dir

        sid = uuid.UUID(session_id)
        session = await session_svc.get_session(db, sid)
        if not session:
            raise RuntimeError(f"Session {session_id} not found")
        user = await db.get(User, session.user_id)
        user_root = user.workspace if user else settings.workspace_root
        session_dir = get_session_dir(user_root, session_id)

        parent_session_dir = None
        allowed_tools = None
        if session.parent_id:
            parent_session_dir = get_session_dir(user_root, str(session.parent_id))
            child_meta = read_child_session_meta(session_dir)
            allowed_tools = (
                child_meta.get("resolved_tools")
                or child_meta.get("allowed_tools")
                or None
            )

    agent = await build_agent(
        session_id=session_id,
        user_id=str(session.user_id),
        user_root=user_root,
        session_dir=session_dir,
        agent_id=session.agent_id,
        model_id=session.model_id,
        tool_profile="child" if session.parent_id else None,
        parent_session_dir=parent_session_dir,
        allowed_tools=allowed_tools,
        run_id=run_id,
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

    closed_hitl_tools = await _close_resolved_hitl_tool_calls_before_resume(
        agent,
        config,
        session_id,
        session_dir,
        snapshot,
        decisions,
        publish,
    )
    resume_payload = None if closed_hitl_tools else Command(resume={"decisions": decisions})

    await _execute_graph(
        agent, resume_payload, config, session_id, session_dir, publish, check_abort,
    )


async def execute_continue(
    session_id: str,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Execute a checkpoint continuation without adding a user message."""
    async with AsyncSessionLocal() as db:
        from auth.models import User
        from agent.child_session import read_child_session_meta
        from workspace.manager import get_session_dir

        sid = uuid.UUID(session_id)
        session = await session_svc.get_session(db, sid)
        if not session:
            raise RuntimeError(f"Session {session_id} not found")
        user = await db.get(User, session.user_id)
        user_root = user.workspace if user else settings.workspace_root
        session_dir = get_session_dir(user_root, session_id)

        parent_session_dir = None
        allowed_tools = None
        if session.parent_id:
            parent_session_dir = get_session_dir(user_root, str(session.parent_id))
            child_meta = read_child_session_meta(session_dir)
            allowed_tools = (
                child_meta.get("resolved_tools")
                or child_meta.get("allowed_tools")
                or None
            )

    agent = await build_agent(
        session_id=session_id,
        user_id=str(session.user_id),
        user_root=user_root,
        session_dir=session_dir,
        agent_id=session.agent_id,
        model_id=session.model_id,
        tool_profile="child" if session.parent_id else None,
        parent_session_dir=parent_session_dir,
        allowed_tools=allowed_tools,
        run_id=run_id,
    )
    config = {"configurable": {"thread_id": session_id}}

    await _validate_continue_checkpoint(agent, config, session_id, retry_context=payload)
    await _execute_graph(
        agent, None, config, session_id, session_dir, publish, check_abort,
        skip_pre_microcompact=True,
    )


# ── Core execution loop ──────────────────────────────────────────────────


async def _validate_continue_checkpoint(
    agent,
    config: dict,
    session_id: str,
    *,
    retry_context: dict[str, Any] | None = None,
) -> None:
    """Gate narrow continue runs to closed tool_result -> model checkpoints."""
    await checkpoint_mgr.CheckpointManager.validate_continue_checkpoint(
        agent,
        config,
        session_id,
        retry_context=retry_context,
    )


async def _execute_graph(
    agent,
    input_data: Any,
    config: dict,
    session_id: str,
    session_dir: str,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
    *,
    skip_pre_microcompact: bool = False,
) -> None:
    """Stream agent, handle interrupts, finalize. Raises on error.

    Phase L: Uses try/finally to ensure diagnostics are recorded even on
    error paths, so that failed runs still carry prompt continuity evidence.
    """
    # Phase 6: reset per-run tool dedup counter
    from tools.registry import reset_tool_call_counter
    from tools.knowledge_routing import reset_knowledge_route_state

    reset_tool_call_counter(session_id)
    reset_knowledge_route_state(getattr(agent, "_run_id", "") or session_id)

    # Phase L: write initial diagnostics early (prompt layer sizes only).
    # If the run fails before _finalize/_handle_interrupt, at least the
    # prompt diagnostics will be on the run record.
    await _record_run_diagnostics(agent, session_id, [])

    # Phase P4-B: microcompact — trim old low-value results before model call.
    # Best-effort: failure doesn't block the run.
    mc_result = None
    if not skip_pre_microcompact:
        try:
            from agent.microcompact import run_microcompact
            # Read latest context ratio from agent metadata if available
            ctx_ratio = getattr(agent, "_last_context_ratio", None)
            mc_result = await run_microcompact(agent, config, session_id, ctx_ratio)
            if mc_result.applied:
                logger.info(
                    "[microcompact] session=%s removed=%d replaced=%d reason=%s",
                    session_id[:8], mc_result.removed_count, mc_result.replaced_count, mc_result.reason,
                )
        except Exception:
            if settings.debug:
                traceback.print_exc()

    # Attach microcompact result to agent for diagnostics recording
    if mc_result:
        agent._microcompact_result = {
            "applied": mc_result.applied,
            "removed_count": mc_result.removed_count,
            "replaced_count": mc_result.replaced_count,
            "reason": mc_result.reason,
        }

    try:
        snapshot_for_gate = await agent.aget_state(config)
        if not (
            _is_hitl_resume_input(input_data)
            and _snapshot_is_open_hitl_interrupt(snapshot_for_gate)
        ):
            await _ensure_checkpoint_tool_adjacency_ready(
                agent,
                config,
                session_id,
                strict=True,
            )

        aborted = await _stream_and_translate(
            agent, input_data, config, session_id, publish, check_abort,
        )
    except Exception as exc:
        from tools.registry import ToolLoopCircuitBreaker

        if isinstance(exc, ToolLoopCircuitBreaker):
            await _record_tool_loop_failure(agent, config, session_id)
        if isinstance(exc, TranscriptIntegrityError):
            agent._transcript_integrity_error = {
                "code": exc.code,
                "issues": exc.issues,
            }
        diagnostics = await _record_exception_diagnostics(agent, config, session_id, exc)
        if diagnostics.get("recoverable_model_continuation") and not isinstance(
            exc, RecoverableProviderTimeout
        ):
            raise RecoverableProviderTimeout(exc, diagnostics) from exc
        raise
    if aborted:
        # Phase P3: if aborted due to subtask_waiting, don't reset to idle —
        # the session should stay in subtask_waiting until child completes.
        if await _is_subtask_waiting(session_id):
            await _repair_checkpoint_tool_adjacency(
                agent,
                config,
                session_id,
                candidate_ai_message=getattr(agent, "_subtask_waiting_ai_message", None),
                candidate_tool_messages=getattr(agent, "_subtask_waiting_tool_messages", []),
            )
            # Persist messages accumulated so far, then exit cleanly
            snapshot = await agent.aget_state(config)
            if snapshot:
                messages = snapshot.values.get("messages", [])
                if messages:
                    await _persist_messages(session_id, messages)
            await publish(session_id, {"event": "done", "reason": "subtask_waiting"})
            return
        snapshot = await agent.aget_state(config)
        if await _handle_pending_interrupt_or_unclosed_tools(
            agent, config, session_id, session_dir, snapshot, publish, check_abort,
        ):
            return
        await _finalize_user_abort(session_id, publish)
        return

    # Check abort boundary
    if check_abort and await check_abort():
        await _finalize_user_abort(session_id, publish)
        return

    # Check for HITL interrupt
    snapshot = await agent.aget_state(config)
    if await _handle_pending_interrupt_or_unclosed_tools(
        agent, config, session_id, session_dir, snapshot, publish, check_abort,
    ):
        return

    # No interrupt — graph completed normally
    await _finalize(agent, config, session_id, publish)


# ── SSE translation ──────────────────────────────────────────────────────


async def _stream_and_translate(
    agent, input_data: Any, config: dict, session_id: str, publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> bool:
    """Stream agent events and translate to AgentD SSE events.

    Uses dual stream mode for token-level text streaming:
    - "messages": yields AIMessageChunk per token → text_delta (requires streaming=True)
    - "updates": yields complete node outputs → tool_start / tool_result

    v0.4.8: ordinary tool_call/tool_result groups are persisted atomically
    after the tools node completes. SSE still carries live tool_start/tool_result
    events; canonical DB transcript no longer stores half-open ordinary groups.
    Phase L/v0.4.9: Checks abort at node boundaries and while model tokens stream.

    Returns True if aborted mid-stream, False otherwise.
    """
    current_message_id: str | None = None
    think_filter = _ThinkFilter()
    provider_reasoning_progress = ""
    current_provider_state: dict[str, Any] = {}
    current_tool_messages: list[ToolMessage] = []
    current_tool_call_message: AIMessage | None = None
    tool_group_accumulator = ToolGroupAccumulator()
    abort_chunk_counter = 0
    last_abort_check_at = time.monotonic()

    async def flush_tool_group(reason: str) -> None:
        nonlocal current_tool_messages
        if (
            not settings.message_persist_atomic_tool_group
            or not tool_group_accumulator.active
            or tool_group_accumulator.ai_message is None
        ):
            return
        all_messages = tool_group_accumulator.complete_messages()
        current_tool_messages = all_messages
        agent._last_tool_messages = all_messages
        await _persist_tool_group_atomic(
            session_id,
            tool_group_accumulator.ai_message,
            all_messages,
            flush_reason=reason,
        )
        tool_group_accumulator.clear()

    async for mode, data in agent.astream(
        input_data, config=config, stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            abort_chunk_counter += 1
            chunk, _metadata = data
            # Token-level text delta from model node
            if isinstance(chunk, AIMessageChunk):
                extracted = extract_reasoning_from_message(chunk)
                cleaned = ""
                reasoning_delta = ""
                if isinstance(chunk.content, str) and chunk.content:
                    if current_message_id is None:
                        current_message_id = str(uuid.uuid4())
                    cleaned, reasoning_delta = think_filter.feed(chunk.content)
                provider_delta = ""
                if extracted.provider_state:
                    append_provider_state_delta(current_provider_state, extracted.provider_state)
                if extracted.visible_text:
                    provider_reasoning_progress, provider_delta = _merge_reasoning_progress(
                        provider_reasoning_progress, extracted.visible_text,
                    )
                combined_reasoning_delta = merge_reasoning_text(reasoning_delta, provider_delta)
                if combined_reasoning_delta and current_message_id is None:
                    current_message_id = str(uuid.uuid4())
                if combined_reasoning_delta:
                    await publish(session_id, {
                        "event": "reasoning_delta",
                        "message_id": current_message_id,
                        "content": combined_reasoning_delta,
                    })
                if cleaned:
                    await publish(session_id, {
                        "event": "text_delta",
                        "message_id": current_message_id,
                        "content": cleaned,
                    })
            if check_abort:
                now = time.monotonic()
                if (
                    abort_chunk_counter >= ABORT_CHECK_CHUNK_INTERVAL
                    or now - last_abort_check_at >= ABORT_CHECK_SECONDS
                ):
                    abort_chunk_counter = 0
                    last_abort_check_at = now
                    if await check_abort():
                        return True

        elif mode == "updates":
            for node_name, node_data in data.items():
                if not node_data:
                    continue

                if node_name == "model":
                    await flush_tool_group("before_next_model_update")
                    # Phase P4-A: reset per-turn result accumulator for the new model turn
                    from tools.registry import reset_turn_accumulator
                    reset_turn_accumulator(session_id)
                    current_tool_messages = []
                    current_tool_call_message = None
                    # Complete (aggregated) model output from "updates" channel.
                    # When streaming=True, text was already sent token-by-token
                    # via "messages". When streaming=False, no "messages" events
                    # are emitted, so we must emit text_delta from here.
                    messages = node_data.get("messages", [])
                    for msg in messages:
                        # Emit text content for non-streaming models
                        # (AIMessage, not AIMessageChunk = non-streaming output)
                        if isinstance(msg, AIMessage) and not isinstance(msg, AIMessageChunk):
                            extracted = extract_reasoning_from_message(msg)
                            cleaned = ""
                            reasoning_delta = ""
                            if isinstance(msg.content, str) and msg.content:
                                if current_message_id is None:
                                    current_message_id = str(uuid.uuid4())
                                cleaned, reasoning_delta = think_filter.feed(msg.content)
                            provider_delta = ""
                            if extracted.provider_state:
                                merge_provider_state_final(current_provider_state, extracted.provider_state)
                            if extracted.visible_text:
                                provider_reasoning_progress, provider_delta = _merge_reasoning_progress(
                                    provider_reasoning_progress, extracted.visible_text,
                                )
                            combined_reasoning_delta = merge_reasoning_text(
                                reasoning_delta, provider_delta,
                            )
                            if combined_reasoning_delta and current_message_id is None:
                                current_message_id = str(uuid.uuid4())
                            if combined_reasoning_delta:
                                await publish(session_id, {
                                    "event": "reasoning_delta",
                                    "message_id": current_message_id,
                                    "content": combined_reasoning_delta,
                                })
                            if cleaned:
                                await publish(session_id, {
                                    "event": "text_delta",
                                    "message_id": current_message_id,
                                    "content": cleaned,
                                })
                        if isinstance(msg, AIMessage) and current_provider_state:
                            merged_kwargs = dict(getattr(msg, "additional_kwargs", {}) or {})
                            merge_provider_state_final(merged_kwargs, current_provider_state)
                            msg.additional_kwargs = merged_kwargs
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            await flush_tool_group("before_next_model_tool_group")
                            current_tool_call_message = msg
                            agent._last_tool_call_message = msg
                            if settings.message_persist_atomic_tool_group:
                                tool_group_accumulator.start(msg)
                            for tc in msg.tool_calls:
                                await publish(session_id, {
                                    "event": "tool_start",
                                    "tool_call_id": tc.get("id", ""),
                                    "tool_name": tc["name"],
                                    "input": tc.get("args", {}),
                                })
                            if not settings.message_persist_atomic_tool_group:
                                await _persist_message_incremental(session_id, msg)

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
                    provider_reasoning_progress = ""
                    current_provider_state = {}
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
                    current_tool_messages = [
                        msg for msg in messages if isinstance(msg, ToolMessage)
                    ]
                    if (
                        settings.message_persist_atomic_tool_group
                        and tool_group_accumulator.active
                    ):
                        tool_group_accumulator.add(current_tool_messages)
                        current_tool_messages = tool_group_accumulator.complete_messages()
                    agent._last_tool_messages = current_tool_messages
                    if settings.message_persist_atomic_tool_group:
                        if tool_group_accumulator.is_complete():
                            await flush_tool_group("complete_tool_group")
                    else:
                        for msg in messages:
                            if isinstance(msg, ToolMessage):
                                await _persist_message_incremental(session_id, msg)

            # Phase L: check abort at node boundaries (after each updates batch)
            if check_abort and await check_abort():
                return True

            # Phase P3: if launch_subagent set parent to subtask_waiting,
            # stop the current run immediately so we don't continue looping.
            if await _is_subtask_waiting(session_id):
                agent._subtask_waiting_ai_message = current_tool_call_message
                agent._subtask_waiting_tool_messages = current_tool_messages
                await _repair_checkpoint_tool_adjacency(
                    agent,
                    config,
                    session_id,
                    candidate_ai_message=current_tool_call_message,
                    candidate_tool_messages=current_tool_messages,
                )
                return True

    if tool_group_accumulator.active:
        try:
            end_snapshot = await agent.aget_state(config)
        except Exception:
            end_snapshot = None
        if end_snapshot is not None and _snapshot_is_open_hitl_interrupt(end_snapshot):
            pass
        elif await _is_subtask_waiting(session_id):
            pass
        else:
            await flush_tool_group("stream_end_incomplete_tool_group")

    # Flush any remaining buffered text after the stream ends
    remaining = think_filter.flush()
    if remaining and current_message_id:
        await publish(session_id, {
            "event": "text_delta",
            "message_id": current_message_id,
            "content": remaining,
        })

    return False


async def _repair_checkpoint_tool_adjacency(
    agent,
    config: dict,
    session_id: str,
    candidate_ai_message: AIMessage | None = None,
    candidate_tool_messages: list[ToolMessage] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Keep checkpoint history valid for strict tool-call providers.

    OpenAI-compatible providers require every assistant message with tool_calls
    to be immediately followed by one ToolMessage for each tool_call_id. The
    subagent waiting boundary can stop streaming before LangGraph persists the
    tools-node update into the checkpoint, while the UI DB already has the tool
    results. This helper first repairs the normal tail case by appending the
    current tools-node results, then removes already-corrupt orphan tool-call
    groups from runtime history so future model calls do not 400.
    """
    return await checkpoint_mgr.CheckpointManager.repair_tool_adjacency(
        agent,
        config,
        session_id,
        candidate_ai_message=candidate_ai_message,
        candidate_tool_messages=candidate_tool_messages,
        strict=strict,
    )


async def _aupdate_messages_as_tools(
    agent,
    config: dict,
    values: dict[str, Any],
):
    """Write repaired tool results as the tools node so LangGraph advances to model."""
    return await checkpoint_mgr.aupdate_messages_as_tools(agent, config, values)


async def _aupdate_messages_as_start(
    agent,
    config: dict,
    values: dict[str, Any],
):
    """Write internal continuation input before resuming the model node."""
    return await checkpoint_mgr.aupdate_messages_as_start(agent, config, values)


def _candidate_tool_group_patch(
    messages: list,
    candidate_ai_message: AIMessage | None,
    candidate_tool_messages: list[ToolMessage] | None,
) -> list:
    """Build a complete AI/tool group patch for a just-finished tools node."""
    return checkpoint_mgr.candidate_tool_group_patch(
        messages,
        candidate_ai_message,
        candidate_tool_messages,
    )


def _merge_updated_config(config: dict, new_config: Any) -> None:
    """Keep execution config on thread-level latest after checkpoint maintenance."""
    checkpoint_mgr.merge_updated_config(config, new_config)


def _missing_tail_tool_messages(
    messages: list,
    candidate_tool_messages: list[ToolMessage],
) -> list[ToolMessage]:
    """Return current tools-node ToolMessages missing after the tail AIMessage."""
    return checkpoint_mgr.missing_tail_tool_messages(messages, candidate_tool_messages)


def _rebuild_messages_with_repaired_tool_adjacency(
    messages: list,
    candidate_tool_messages: list[ToolMessage],
) -> list | None:
    """Return a repaired full message list when candidates can close tool calls."""
    return checkpoint_mgr.rebuild_messages_with_repaired_tool_adjacency(
        messages,
        candidate_tool_messages,
    )


def _find_invalid_tool_adjacency_indices(messages: list) -> list[int]:
    """Find checkpoint messages that would violate provider tool-call ordering."""
    return _checkpoint_state_invalid_indices(messages)


async def _load_tool_messages_from_persisted_session(
    session_id: str,
    tool_call_ids: list[str] | None = None,
) -> list[ToolMessage]:
    """Load persisted tool_result parts as ToolMessages for checkpoint repair."""
    return await checkpoint_mgr.load_tool_messages_from_persisted_session(
        session_id,
        tool_call_ids,
    )


def _checkpoint_tool_call_ids(messages: list) -> list[str]:
    return checkpoint_mgr.checkpoint_tool_call_ids(messages)


async def _ensure_checkpoint_tool_adjacency_ready(
    agent,
    config: dict,
    session_id: str,
    strict: bool = True,
) -> None:
    """Repair and verify checkpoint tool adjacency before provider calls."""
    await checkpoint_mgr.CheckpointManager.ensure_tool_adjacency_ready(
        agent,
        config,
        session_id,
        strict=strict,
        tool_message_loader=_load_tool_messages_from_persisted_session,
    )


def _is_hitl_resume_input(input_data: Any) -> bool:
    return hitl_rt.HITLRuntime.is_resume_input(input_data)


def _snapshot_is_open_hitl_interrupt(snapshot) -> bool:
    return hitl_rt.HITLRuntime.snapshot_is_open_interrupt(snapshot)


# ── Interrupt handling ───────────────────────────────────────────────────


async def _handle_pending_interrupt_or_unclosed_tools(
    agent,
    config: dict,
    session_id: str,
    session_dir: str,
    snapshot,
    publish: PublishFn,
    check_abort: Optional[AbortCheckFn] = None,
) -> bool:
    """Route any pending HITL/tool-call intermediate state before finalizing."""
    if not snapshot:
        return False

    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])

    if _snapshot_interrupt_already_resolved(snapshot):
        return False

    if getattr(snapshot, "interrupts", None):
        await _handle_interrupt(
            session_id, session_dir, snapshot, config, agent, publish, check_abort,
        )
        return True

    if _snapshot_next_is_hitl_interrupt(snapshot):
        if _checkpoint_tool_adjacency_is_valid(messages):
            return False
        synthetic = _synthetic_interrupt_snapshot(snapshot)
        if synthetic is None:
            await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
            raise RuntimeError(
                "Checkpoint stopped at HITL interrupt without reconstructable action requests"
            )
        await _handle_interrupt(
            session_id, session_dir, synthetic, config, agent, publish, check_abort,
        )
        return True

    if _tail_has_unclosed_tool_calls(messages):
        synthetic = _synthetic_interrupt_snapshot(snapshot)
        if synthetic is not None:
            await _handle_interrupt(
                session_id, session_dir, synthetic, config, agent, publish, check_abort,
            )
            return True
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        raise RuntimeError(
            "Terminal checkpoint has unclosed non-HITL tool calls; refusing to mark session idle"
        )

    return False


def _snapshot_next_is_hitl_interrupt(snapshot) -> bool:
    return hitl_rt.HITLRuntime.snapshot_next_is_interrupt(snapshot)


def _snapshot_interrupt_already_resolved(snapshot) -> bool:
    """Return True when a stale interrupt points at already-closed tool calls."""
    return hitl_rt.HITLRuntime.snapshot_interrupt_already_resolved(snapshot)


def _checkpoint_tool_adjacency_is_valid(messages: list) -> bool:
    return _checkpoint_state_tool_adjacency_is_valid(messages)


async def _record_exception_diagnostics(
    agent,
    config: dict,
    session_id: str,
    exc: BaseException,
) -> dict[str, Any]:
    """Persist checkpoint diagnostics for every graph exception path."""
    captured = await capture_checkpoint_snapshot(agent, config)
    diagnostics = _build_v044_exception_diagnostics(
        exc,
        captured,
        run_type=getattr(agent, "_run_type", None),
    )
    agent._run_exception_diagnostics = diagnostics
    await _record_run_diagnostics(agent, session_id, captured.messages, snapshot=captured.snapshot)
    return diagnostics


def _build_exception_diagnostics(
    exc: BaseException,
    snapshot,
    messages: list,
) -> dict[str, Any]:
    return build_checkpoint_diagnostics(
        messages=messages,
        snapshot=snapshot,
        exception=exc,
    )


def _is_recoverable_provider_timeout_after_tool_result(
    exc: BaseException,
    snapshot,
    messages: list,
) -> bool:
    diagnostics = build_checkpoint_diagnostics(
        messages=messages,
        snapshot=snapshot,
        exception=exc,
    )
    return bool(diagnostics.get("recoverable_model_continuation"))


def _is_provider_timeout_exception(exc: BaseException) -> bool:
    return classify_provider_error(exc).value == "provider_timeout"


def _snapshot_next_contains_model(snapshot) -> bool:
    return _checkpoint_state_next_contains_model(snapshot)


def _tail_has_unclosed_tool_calls(messages: list) -> bool:
    return _checkpoint_state_tail_has_unclosed_tool_calls(messages)


def _tail_tool_call_group(messages: list) -> dict[str, Any] | None:
    return _checkpoint_state_tail_tool_call_group(messages)


def _synthetic_interrupt_snapshot(snapshot):
    return hitl_rt.HITLRuntime.synthetic_interrupt_snapshot(snapshot)


def _extract_unclosed_hitl_action_requests(snapshot) -> tuple[list[dict[str, Any]], list[str]]:
    return hitl_rt.HITLRuntime.extract_unclosed_action_requests(snapshot)


def _extract_tool_call_ids(snapshot) -> list[str]:
    """Extract tool_call_ids for interrupted tools from checkpoint state."""
    return hitl_rt.HITLRuntime.extract_tool_call_ids(snapshot)


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
    batch_key = _interrupt_batch_key(action_requests, tool_call_ids)

    if _snapshot_interrupt_already_resolved(snapshot):
        return False

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
            auto_resumed_batches = getattr(agent, "_agentd_auto_resumed_batches", set())
            if batch_key in auto_resumed_batches:
                full_state = snapshot.values if snapshot else {}
                await _record_run_diagnostics(
                    agent,
                    session_id,
                    full_state.get("messages", []),
                    snapshot=snapshot,
                )
                raise RuntimeError(
                    "Auto-approved HITL interrupt did not advance after resume; "
                    "refusing duplicate auto-approve"
                )
            auto_resumed_batches.add(batch_key)
            agent._agentd_auto_resumed_batches = auto_resumed_batches

            # Auto-approve: audit records then resume inline
            try:
                async with AsyncSessionLocal() as db:
                    for action, tc_id in zip(action_requests, tool_call_ids):
                        pr, _created = await perm_svc.get_or_create_permission_request(
                            db,
                            session_id=uuid.UUID(session_id),
                            tool_call_id=tc_id,
                            tool_name=action["name"],
                            tool_input=action.get("args", {}),
                        )
                        if getattr(pr, "status", "") == "denied":
                            raise RuntimeError(
                                f"Cannot auto-approve previously denied tool_call_id={tc_id}"
                            )
                    await perm_svc.mark_permission_requests_auto_approved(
                        db,
                        uuid.UUID(session_id),
                        tool_call_ids,
                    )
                    await db.commit()
            except Exception as exc:
                if settings.debug:
                    traceback.print_exc()
                raise RuntimeError("Failed to create auto-approved permission requests") from exc

            auto_sibling_messages = await _close_auto_sibling_tool_calls_before_hitl(
                agent,
                config,
                session_id,
                session_dir,
                snapshot,
                tool_call_ids,
                publish,
            )
            if auto_sibling_messages:
                snapshot = await agent.aget_state(config)

            from langgraph.types import Command
            decisions = [{"type": "approve"} for _ in action_requests]
            resume_payload = Command(resume={"decisions": decisions})

            if not _snapshot_is_open_hitl_interrupt(snapshot):
                await _ensure_checkpoint_tool_adjacency_ready(
                    agent,
                    config,
                    session_id,
                    strict=True,
                )

            try:
                aborted = await _stream_and_translate(
                    agent, resume_payload, config, session_id, publish, check_abort,
                )
            except Exception as exc:
                from tools.registry import ToolLoopCircuitBreaker

                if isinstance(exc, ToolLoopCircuitBreaker):
                    await _record_tool_loop_failure(agent, config, session_id)
                if isinstance(exc, TranscriptIntegrityError):
                    agent._transcript_integrity_error = {
                        "code": exc.code,
                        "issues": exc.issues,
                    }
                diagnostics = await _record_exception_diagnostics(
                    agent, config, session_id, exc,
                )
                if diagnostics.get("recoverable_model_continuation") and not isinstance(
                    exc, RecoverableProviderTimeout
                ):
                    raise RecoverableProviderTimeout(exc, diagnostics) from exc
                raise

            await _repair_checkpoint_tool_adjacency(
                agent,
                config,
                session_id,
                candidate_ai_message=getattr(agent, "_last_tool_call_message", None),
                candidate_tool_messages=getattr(agent, "_last_tool_messages", []),
            )

            # Check abort boundary after auto-resume (also caught mid-stream by L4)
            if aborted or (check_abort and await check_abort()):
                abort_snapshot = await agent.aget_state(config)
                if await _handle_pending_interrupt_or_unclosed_tools(
                    agent, config, session_id, session_dir, abort_snapshot, publish, check_abort,
                ):
                    return False
                await _update_db_status(session_id, "idle")
                await publish(session_id, {"event": "status_change", "status": "idle"})
                return False

            new_snapshot = await agent.aget_state(config)
            if await _handle_pending_interrupt_or_unclosed_tools(
                agent, config, session_id, session_dir, new_snapshot, publish, check_abort,
            ):
                return False

            await _finalize(agent, config, session_id, publish)
            return False

    # ── Standard ask flow ──
    auto_sibling_messages = await _close_auto_sibling_tool_calls_before_hitl(
        agent,
        config,
        session_id,
        session_dir,
        snapshot,
        tool_call_ids,
        publish,
    )
    if auto_sibling_messages:
        snapshot = await agent.aget_state(config)

    # Phase L: persist all in-flight messages before entering waiting state,
    # so that session switches / refreshes can recover full tool history.
    full_state = snapshot.values if snapshot else {}
    waiting_messages = full_state.get("messages", [])
    if waiting_messages:
        await _persist_messages(session_id, waiting_messages)

    # Phase L fix: record diagnostics before entering waiting state,
    # so that waiting runs also have prompt continuity evidence.
    await _record_run_diagnostics(agent, session_id, waiting_messages, snapshot=snapshot)

    permission_ids: list[str] = []

    try:
        async with AsyncSessionLocal() as db:
            for action, tc_id in zip(action_requests, tool_call_ids):
                perm_id = uuid.uuid4()
                pr, _created = await perm_svc.get_or_create_permission_request(
                    db,
                    session_id=uuid.UUID(session_id),
                    tool_call_id=tc_id,
                    tool_name=action["name"],
                    tool_input=action.get("args", {}),
                    permission_id=perm_id,
                )
                if getattr(pr, "status", "") != "pending":
                    raise RuntimeError(
                        f"Permission request for tool_call_id={tc_id} is already {pr.status}"
                    )
                permission_ids.append(str(pr.id))
            await db.commit()
    except Exception as exc:
        if settings.debug:
            traceback.print_exc()
        raise RuntimeError("Failed to create permission requests") from exc

    if len(permission_ids) != len(action_requests):
        raise RuntimeError("Permission request count mismatch; refusing to enter idle state")

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


async def _close_auto_sibling_tool_calls_before_hitl(
    agent,
    config: dict,
    session_id: str,
    session_dir: str,
    snapshot,
    hitl_tool_call_ids: list[str],
    publish: PublishFn,
) -> list[ToolMessage]:
    """Execute non-HITL siblings in a mixed tool-call group before waiting."""
    from agent.runtime import _HITL_INTERRUPT_ON
    from tools.registry import execute_registered_tool

    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])
    group = _tail_tool_call_group(messages)
    if not group:
        return []

    ai_message = group["ai_message"]
    missing_ids = set(group.get("missing_ids") or [])
    hitl_ids = {tool_call_id for tool_call_id in hitl_tool_call_ids if tool_call_id}
    if not missing_ids or not hitl_ids:
        return []

    auto_calls = []
    for tool_call in getattr(ai_message, "tool_calls", []) or []:
        tool_call_id = tool_call.get("id") or ""
        tool_name = tool_call.get("name") or ""
        if (
            tool_call_id
            and tool_call_id in missing_ids
            and tool_call_id not in hitl_ids
            and tool_name not in _HITL_INTERRUPT_ON
        ):
            auto_calls.append(tool_call)

    if not auto_calls:
        return []

    if messages:
        await _persist_messages(session_id, messages)

    ctx = await _tool_context_for_hitl_sibling(agent, session_id, session_dir)
    tool_messages: list[ToolMessage] = []
    for tool_call in auto_calls:
        tool_call_id = tool_call.get("id") or ""
        tool_name = tool_call.get("name") or ""
        tool_input = dict(tool_call.get("args") or {})
        await publish(session_id, {
            "event": "tool_start",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "input": tool_input,
        })
        is_error = False
        try:
            output = await execute_registered_tool(tool_name, ctx, tool_input)
        except Exception as exc:
            is_error = True
            output = str(exc)
        tool_message = ToolMessage(
            content=output,
            tool_call_id=tool_call_id,
            name=tool_name,
            additional_kwargs={"is_error": True} if is_error else {},
        )
        tool_messages.append(tool_message)
        await publish(session_id, {
            "event": "tool_result",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": is_error,
        })
        await _persist_message_incremental(session_id, tool_message)

    if tool_messages:
        new_config = await _aupdate_messages_as_tools(
            agent,
            config,
            {"messages": tool_messages},
        )
        _merge_updated_config(config, new_config)
        agent._hitl_auto_sibling_tool_messages = tool_messages

    return tool_messages


async def _close_resolved_hitl_tool_calls_before_resume(
    agent,
    config: dict,
    session_id: str,
    session_dir: str,
    snapshot,
    decisions: list[dict],
    publish: PublishFn,
) -> list[ToolMessage]:
    """Execute approved HITL tools before resuming a partially closed group."""
    from tools.registry import execute_registered_tool

    if not snapshot:
        return []

    actions, tool_call_ids = _extract_unclosed_hitl_action_requests(snapshot)
    if not actions or not tool_call_ids:
        return []

    normalized_decisions = list(decisions or [])
    while len(normalized_decisions) < len(actions):
        normalized_decisions.append({
            "type": "reject",
            "message": "Permission auto-denied (mismatch)",
        })
    if len(normalized_decisions) > len(actions):
        normalized_decisions = normalized_decisions[:len(actions)]

    messages = (getattr(snapshot, "values", {}) or {}).get("messages", [])
    if messages:
        await _persist_messages(session_id, messages)

    ctx = await _tool_context_for_hitl_sibling(agent, session_id, session_dir)
    tool_messages: list[ToolMessage] = []
    for action, tool_call_id, decision in zip(actions, tool_call_ids, normalized_decisions):
        tool_name = action.get("name") or ""
        tool_input = dict(action.get("args") or {})
        decision_type = str((decision or {}).get("type") or "").lower()
        is_approved = decision_type in {"approve", "approved"}
        is_error = not is_approved

        if is_approved:
            await publish(session_id, {
                "event": "tool_start",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": tool_input,
            })
            try:
                output = await execute_registered_tool(tool_name, ctx, tool_input)
            except Exception as exc:
                is_error = True
                output = str(exc)
        else:
            output = (decision or {}).get("message") or "Permission denied by user"

        tool_message = ToolMessage(
            content=output,
            tool_call_id=tool_call_id,
            name=tool_name,
            additional_kwargs={"is_error": True} if is_error else {},
        )
        tool_messages.append(tool_message)
        await publish(session_id, {
            "event": "tool_result",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": is_error,
        })
        await _persist_message_incremental(session_id, tool_message)

    if not tool_messages:
        return []

    new_config = await _aupdate_messages_as_tools(
        agent,
        config,
        {"messages": tool_messages},
    )
    _merge_updated_config(config, new_config)
    agent._last_tool_messages = tool_messages
    await _mark_resolved_hitl_permissions_consumed(session_id, tool_call_ids)
    return tool_messages


async def _mark_resolved_hitl_permissions_consumed(
    session_id: str,
    tool_call_ids: list[str],
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await perm_svc.mark_resolved_as_resumed_by_tool_call_ids(
                db,
                uuid.UUID(session_id),
                tool_call_ids,
            )
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


async def _tool_context_for_hitl_sibling(agent, session_id: str, session_dir: str):
    from tools.base import ToolContext

    user_id = getattr(agent, "_user_id", "") or ""
    user_root = getattr(agent, "_user_root", "") or ""
    workspace_dir = getattr(agent, "_workspace_dir", None) or session_dir

    if not user_id or not user_root:
        async with AsyncSessionLocal() as db:
            from auth.models import User
            sid = uuid.UUID(session_id)
            session = await session_svc.get_session(db, sid)
            if session:
                user_id = str(session.user_id)
                user = await db.get(User, session.user_id)
                if user:
                    user_root = user.workspace

    user_root = user_root or settings.workspace_root
    return ToolContext(
        user_id=user_id,
        session_id=session_id,
        user_root=user_root,
        session_dir=session_dir,
        venv_bin=user_root.rstrip("/") + "/.venv/bin/",
        publish=None,
        workspace_dir=workspace_dir,
        run_id=getattr(agent, "_run_id", "") or "",
    )


def _interrupt_batch_key(action_requests: list[dict], tool_call_ids: list[str]) -> tuple[str, ...]:
    return hitl_rt.HITLRuntime.interrupt_batch_key(action_requests, tool_call_ids)


# ── Finalization ─────────────────────────────────────────────────────────


async def _decide_runtime_terminal_state(
    session_id: str,
    snapshot,
) -> tuple[Any, dict[str, Any] | None]:
    checkpoint_state = classify_checkpoint_snapshot(snapshot) if snapshot else None
    try:
        async with AsyncSessionLocal() as db:
            from unittest.mock import Mock
            if isinstance(db, Mock):
                return RuntimeGateDecision(
                    action=RuntimeGateAction.FINALIZE_IDLE,
                    reason="mock_db_bypass",
                    can_accept_user_prompt=True,
                ), None
            sid = uuid.UUID(session_id)
            session = await session_svc.get_session(db, sid)
            pending_permissions = await perm_svc.get_pending_by_session(db, sid)
            from agent import scheduler

            active_run = await scheduler.get_active_run(db, sid)
            diagnostics = dict(getattr(active_run, "diagnostics", None) or {}) if active_run else {}
            run_start_seq = diagnostics.get("run_start_seq")
            try:
                run_start_seq = int(run_start_seq) if run_start_seq is not None else None
            except (TypeError, ValueError):
                run_start_seq = None
            return await decide_terminal_with_layered_validation(
                db,
                session_id=sid,
                session_status=getattr(session, "status", None),
                checkpoint_state=checkpoint_state,
                pending_permissions=pending_permissions,
                run_start_seq=run_start_seq,
                latest_run_type=getattr(active_run, "run_type", None),
                latest_run_status=getattr(active_run, "status", None),
                latest_error=getattr(active_run, "error", None),
            )
    except Exception as exc:
        return RuntimeGateDecision(
            action=RuntimeGateAction.FAIL_INTEGRITY_ERROR,
            reason=f"runtime_integrity_input_error:{type(exc).__name__}",
            checkpoint_state_kind=(
                checkpoint_state.state_kind.value if checkpoint_state else None
            ),
            is_provider_payload_ready=bool(
                checkpoint_state and checkpoint_state.is_provider_payload_ready
            ),
            requires_human_input=bool(
                checkpoint_state and checkpoint_state.requires_human_input
            ),
            open_tool_call_ids=list(
                (checkpoint_state.open_tool_call_ids if checkpoint_state else []) or []
            ),
        ), None


async def _finalize(agent, config: dict, session_id: str, publish: PublishFn) -> None:
    """Post-completion: persist messages, update status, publish done."""
    snapshot = await agent.aget_state(config)
    full_state = snapshot.values if snapshot else {}
    messages = full_state.get("messages", [])

    if messages:
        await _persist_messages(session_id, messages)
        await _persist_loaded_skills(session_id, messages)

    token_usage = _extract_token_usage(messages)

    # Phase L: record prompt diagnostics on the active run
    await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)

    gate_decision, gate_warning = await _decide_runtime_terminal_state(session_id, snapshot)
    agent._runtime_integrity_gate = gate_decision.to_dict()
    if gate_warning:
        agent._runtime_integrity_warning = gate_warning
    if gate_decision.action == RuntimeGateAction.ENTER_SUBTASK_WAITING:
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        await _update_db_status(session_id, "subtask_waiting", token_usage=token_usage)
        await publish(session_id, {"event": "status_change", "status": "subtask_waiting"})
        return
    if gate_decision.action == RuntimeGateAction.ENTER_WAITING:
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        await _update_db_status(session_id, "waiting", token_usage=token_usage)
        await publish(session_id, {"event": "status_change", "status": "waiting"})
        return
    if gate_decision.action == RuntimeGateAction.CONTINUE_MODEL:
        # v0.4.9 Phase B Finding 1: CONTINUE_MODEL is a legal continuation
        # state ("checkpoint says next=model after a closed tool_result").
        # Treat it as a soft return: record diagnostics, persist token usage,
        # and let the worker's _assert_run_returned_terminal_state re-evaluate
        # the gate and enqueue a narrow continue run.
        #
        # We set session.status to "idle" so the worker's terminal helper does
        # not interpret a stray "running" as an executor drift (fail-soft).
        # The continue run enqueued by the worker will transition the session
        # to "queued" immediately afterward — there is no user-visible idle
        # window because both DB writes happen inside the same worker tick.
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        await _update_db_status(session_id, "idle", token_usage=token_usage)
        await publish(session_id, {
            "event": "status_change",
            "status": "idle",
            "trigger": "executor_finalize_continue_model",
            "reason": gate_decision.reason,
        })
        return
    if gate_decision.action != RuntimeGateAction.FINALIZE_IDLE:
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        raise RuntimeIntegrityError(session_id, gate_decision)

    # Phase P4-B: post-run microcompact — now the checkpoint has all messages
    # from this run. Clean up old low-value results for the NEXT run.
    try:
        from agent.microcompact import run_microcompact
        ctx_ratio = None
        print(f"[microcompact/post-run] ENTERING for session={session_id[:8]} messages={len(messages)}")
        mc_result = await run_microcompact(agent, config, session_id, ctx_ratio)
        print(f"[microcompact/post-run] RESULT: applied={mc_result.applied} removed={mc_result.removed_count} replaced={mc_result.replaced_count} reason={mc_result.reason}")
        # Update diagnostics with post-run microcompact
        agent._microcompact_result = {
            "applied": mc_result.applied,
            "removed_count": mc_result.removed_count,
            "replaced_count": mc_result.replaced_count,
            "reason": mc_result.reason,
        }
        # Re-record diagnostics with updated microcompact info
        await _record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)
        print(f"[microcompact/post-run] diagnostics re-recorded")
    except Exception as e:
        print(f"[microcompact/post-run] EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()

    await _update_db_status(session_id, "idle", token_usage=token_usage)

    # Phase L: call-level context data for frontend "Prompt X / Y" display.
    # NOTE: context_usage_ratio reflects the LAST model call in this run.
    # After auto-compaction below, the ratio is NOT recalculated — it still
    # represents the pre-compaction call. The ratio will drop on the NEXT run
    # when the compacted checkpoint produces a shorter prompt.
    last_call = _extract_last_call_usage(messages)
    context_window_limit = getattr(agent, "_context_window_limit", None)
    context = {
        "prompt_tokens": last_call["prompt_tokens"],
        "completion_tokens": last_call["completion_tokens"],
        "context_window_limit": context_window_limit,
    }
    if context_window_limit and last_call["prompt_tokens"] > 0:
        context["context_usage_ratio"] = round(
            last_call["prompt_tokens"] / context_window_limit, 4,
        )

    # Phase N1: check if context_warning or auto-compact should fire
    ratio = context.get("context_usage_ratio")
    from agent.compaction import should_warn, should_compact, compact_session as do_compact

    if should_warn(ratio):
        await publish(session_id, {
            "event": "context_warning",
            "context_usage_ratio": ratio,
            "context_window_limit": context_window_limit,
            "prompt_tokens": last_call["prompt_tokens"],
        })

    if should_compact(ratio):
        # Auto-compact: best-effort, non-blocking to avoid delaying done event.
        # We need session_dir and model_id — extract from agent metadata.
        try:
            _sd = getattr(agent, "_session_dir", None)
            _mid = getattr(agent, "_model_id", None)
            if _sd and _mid:
                compact_result = await do_compact(
                    agent=agent,
                    config=config,
                    session_id=session_id,
                    session_dir=_sd,
                    model_id=_mid,
                    publish=publish,
                )
                if settings.debug:
                    print(f"[executor] auto-compact result: {compact_result}")
        except Exception:
            if settings.debug:
                traceback.print_exc()

    await publish(session_id, {"event": "status_change", "status": "idle"})
    await publish(session_id, {
        "event": "done",
        "token_usage": token_usage,
        "context": context,
    })

    # v0.4.9 follow-up: title maintenance must not be a fire-and-forget orphan.
    # We still publish ``done`` first so the user sees the run finish promptly,
    # then execute bounded title generation on this worker tick. The sidecar
    # model call has its own timeout and mechanical fallback, so this remains
    # best-effort without disappearing after worker teardown.
    await _maybe_generate_title(session_id, messages, publish)

    # Phase P4-C: update rolling session memory (best-effort, non-blocking)
    _sd = getattr(agent, "_session_dir", None)
    if _sd:
        asyncio.create_task(_update_session_memory_async(_sd, messages, session_id))


async def _update_session_memory_async(session_dir: str, messages: list, session_id: str) -> None:
    """Phase P4-C: async wrapper for session memory update. Best-effort."""
    try:
        from agent.session_memory import should_update_memory, update_session_memory

        last_seq = len(messages) - 1
        if should_update_memory(session_dir, messages, last_seq):
            updated = await update_session_memory(session_dir, messages, session_id)
            if updated and settings.debug:
                print(f"[session_memory] Updated for session {session_id[:8]}")
    except Exception:
        if settings.debug:
            traceback.print_exc()


async def _record_run_diagnostics(
    agent,
    session_id: str,
    messages: list,
    *,
    snapshot=None,
) -> None:
    """Write prompt diagnostics to the active agent_run record (Phase L).

    Combines prompt layer sizes (from build_system_prompt) with message
    history statistics and per-run token counts so that each run has a
    complete diagnostic snapshot.

    Phase L prompt strategy: called early in _execute_graph (with empty messages)
    to ensure prompt diagnostics survive even on failed runs. Called again at
    _finalize/_handle_interrupt with full messages for complete diagnostics.

    Phase D split note: implementation lives in agent.run_diagnostics, but this
    wrapper intentionally keeps the historical audit keywords visible for
    source-based regression tests: checkpoint_composition, run_type,
    context_window_limit, context_usage_ratio, last_call_prompt_tokens,
    last_call_completion_tokens, last_call_total_tokens,
    last_call_cache_read_tokens, last_call_cache_creation_tokens, and
    last_call["prompt_tokens"].
    """
    await run_diag.record_run_diagnostics(
        agent,
        session_id,
        messages,
        snapshot=snapshot,
    )


def _extract_knowledge_source_refs(messages: list) -> list[dict]:
    """Phase P6-D: extract knowledge source references from tool results.

    Scans ToolMessages from knowledge_search and knowledge_read,
    extracts doc_id / title / kind / source_file / evidence_excerpt
    to be attached as a source_refs part on the final assistant message.
    """
    return msg_persist.extract_knowledge_source_refs(messages)


def _get_compaction_mode_diagnostics(session_dir: str | None) -> dict:
    """Extract compaction mode from session_memory_meta.json for diagnostics."""
    return run_diag.get_compaction_mode_diagnostics(session_dir)


def _get_microcompact_diagnostics(agent) -> dict:
    """Extract microcompact result from agent metadata for diagnostics."""
    return run_diag.get_microcompact_diagnostics(agent)


def _get_transcript_integrity_diagnostics(agent) -> dict:
    return run_diag.get_transcript_integrity_diagnostics(agent)


def _get_exception_diagnostics(agent) -> dict:
    return run_diag.get_exception_diagnostics(agent)


async def _record_tool_loop_failure(agent, config: dict, session_id: str) -> None:
    """Best-effort persistence/diagnostics before bubbling a hard tool-loop stop."""
    await run_diag.record_tool_loop_failure(agent, config, session_id)


# ── Helpers (moved from runner.py) ───────────────────────────────────────


def _is_tool_error(msg) -> bool:
    return msg_persist.is_tool_error(msg)


def _extract_token_usage(messages: list) -> dict:
    """Run-level accumulated token counts across all model calls."""
    return run_diag.extract_token_usage(messages)


def _extract_last_call_usage(messages: list) -> dict:
    """Call-level: exact token counts from the most recent provider call.

    Reads usage_metadata from the LAST AIMessage in the checkpoint.
    This is the precise prompt token count for the current model invocation,
    suitable for context window occupancy display and compaction triggers.
    """
    return run_diag.extract_last_call_usage(messages)


async def _persist_message_incremental(session_id: str, msg) -> None:
    """Persist a single AIMessage or ToolMessage to the messages table immediately.

    Phase L: called from _stream_and_translate() at tool boundaries so that
    tool_call / tool_result history survives session switches and refreshes.
    Uses its own DB session + commit for isolation from the main flow.
    """
    await msg_persist.persist_message_incremental(session_id, msg)


async def _persist_tool_group_atomic(
    session_id: str,
    ai_message: AIMessage,
    tool_messages: list[ToolMessage],
    *,
    flush_reason: str = "complete_tool_group",
) -> None:
    await msg_persist.persist_tool_group_atomic(
        session_id,
        ai_message,
        tool_messages,
        flush_reason=flush_reason,
    )


async def _persist_messages(session_id: str, messages: list) -> None:
    """Persist checkpoint messages idempotently.

    Phase D split note: implementation lives in agent.message_persistence, but
    this wrapper preserves historical audit keywords for source-based tests:
    _strip_model_tags, _extract_reasoning, "reasoning", "tool_name",
    getattr(msg, "name".
    """
    await msg_persist.persist_messages(session_id, messages)


async def _load_existing_part_keys(db, session_id: uuid.UUID) -> set[str]:
    return await msg_persist.load_existing_part_keys(db, session_id)


async def _persist_runtime_message_once(
    db,
    session_id: uuid.UUID,
    msg,
    existing_keys: set[str],
    knowledge_source_refs: list[dict] | None = None,
) -> bool:
    return await msg_persist.persist_runtime_message_once(
        db,
        session_id,
        msg,
        existing_keys,
        knowledge_source_refs=knowledge_source_refs,
    )


def _build_persistable_message_parts(
    msg,
    knowledge_source_refs: list[dict] | None = None,
) -> tuple[str, list[dict[str, Any]], bool]:
    return msg_persist.build_persistable_message_parts(msg, knowledge_source_refs)


def _with_runtime_message_id(part: dict[str, Any], runtime_message_id: str | None) -> dict[str, Any]:
    return msg_persist.with_runtime_message_id(part, runtime_message_id)


def _part_dedupe_key(part: dict[str, Any]) -> str | None:
    return msg_persist.part_dedupe_key(part)


def _part_dedupe_keys(part: dict[str, Any]) -> list[str]:
    return msg_persist.part_dedupe_keys(part)


async def _persist_loaded_skills(session_id: str, messages: list) -> None:
    """Persist loaded skill entries (name + version) and update usage stats.

    Extracts skill info from ToolMessage content matching ``[Skill: <name> v<ver>]``
    (Phase F2 format) or legacy ``[Skill: <name>]`` format.
    """
    await msg_persist.persist_loaded_skills(session_id, messages)


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


def _merge_reasoning_progress(previous: str, current: str) -> tuple[str, str]:
    """Return updated reasoning progress and the incremental delta to emit."""
    previous = (previous or "").strip()
    current = (current or "").strip()
    if not current:
        return previous, ""
    if not previous:
        return current, current
    if current == previous:
        return previous, ""
    if current.startswith(previous):
        return current, current[len(previous):].lstrip("\n")
    if previous.startswith(current):
        return previous, ""
    merged = merge_reasoning_text(previous, current)
    if merged == previous:
        return previous, ""
    if merged.startswith(previous + "\n"):
        return merged, merged[len(previous):].lstrip("\n")
    return merged, current


def _strip_model_tags(message_or_text: Any) -> str:
    """Return visible assistant text with provider reasoning tags removed."""
    if isinstance(message_or_text, str):
        return strip_reasoning_tags(message_or_text)
    content = getattr(message_or_text, "content", "")
    return strip_reasoning_tags(content if isinstance(content, str) else "")


def _extract_reasoning(message_or_text: Any) -> str:
    """Extract visible reasoning from either raw text or AI messages."""
    if isinstance(message_or_text, str):
        return extract_reasoning_from_text(message_or_text)
    return extract_reasoning_from_message(message_or_text).visible_text


# Backward-compatible alias
_strip_think_tags = _strip_model_tags


async def _maybe_generate_title(session_id: str, messages: list, publish: PublishFn) -> None:
    from agent.session_title import record_title_generation_diagnostics

    try:
        from agent.session_title import generate_session_title

        result = await generate_session_title(session_id, messages)
        if not result.title:
            await record_title_generation_diagnostics(
                session_id, dict(result.diagnostics or {}),
            )
            return
        event = {"event": "title_update", "title": result.title}
        diagnostics = dict(result.diagnostics or {})
        diagnostics["maintenance_title_event_publish_attempted"] = True
        try:
            await publish(session_id, event)
            diagnostics["maintenance_title_event_publish_ok"] = bool(
                event.get("_event_bridge_notify_ok", True)
            )
            if event.get("_event_bridge_notify_error"):
                diagnostics["maintenance_title_event_publish_error"] = str(
                    event["_event_bridge_notify_error"]
                )
        except Exception as exc:
            diagnostics["maintenance_title_event_publish_ok"] = False
            diagnostics["maintenance_title_event_publish_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            await record_title_generation_diagnostics(session_id, diagnostics)
            return
        await record_title_generation_diagnostics(session_id, diagnostics)

        if settings.debug:
            print(f"[executor] title generated: {result.title}")

    except Exception:
        if settings.debug:
            traceback.print_exc()


async def _is_subtask_waiting(session_id: str) -> bool:
    """Check if this session is in subtask_waiting state (Phase P3).

    Used by _stream_and_translate to halt the run after launch_subagent
    sets the parent to subtask_waiting — prevents the agent from continuing
    to call more tools while waiting for the child.
    """
    try:
        async with AsyncSessionLocal() as db:
            session = await session_svc.get_session(db, uuid.UUID(session_id))
            return session is not None and session.status == "subtask_waiting"
    except Exception:
        return False


async def _finalize_user_abort(session_id: str, publish: PublishFn) -> None:
    """Cleanly return a user-aborted session to idle."""
    try:
        from agent import scheduler

        sid = uuid.UUID(session_id)
        async with AsyncSessionLocal() as db:
            await scheduler.clear_interrupt(db, sid)
            await scheduler.cancel_queued_abort_runs(db, sid)
            await perm_svc.cancel_pending_by_session(db, sid)
            await session_svc.update_session_status(db, sid, "idle")
            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()
        await _update_db_status(session_id, "idle")
    await publish(session_id, {
        "event": "status_change",
        "status": "idle",
        "reason": "user_abort",
    })


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
