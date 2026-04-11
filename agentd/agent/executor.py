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
import traceback
import uuid
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

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


_SUBTASK_CONTINUATION_MARKER = "[Subtask Continuation - internal only]"
_SUBTASK_RESULT_BRIDGE_KIND = "subtask_result_bridge"
_SUBTASK_CONTINUATION_PROMPT = (
    "Continue from the bridged subtask result already present in the conversation."
)


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
        # Subtask bridge: inject the child result as assistant semantics,
        # then add a tiny internal continuation nudge as a HumanMessage.
        # This keeps the bridged result out of system-role history while
        # avoiding a fake user bubble in persisted messages.
        initial_input = {"messages": [
            AIMessage(
                content=user_message,
                additional_kwargs={"agentd_internal": _SUBTASK_RESULT_BRIDGE_KIND},
            ),
            HumanMessage(content=(
                _SUBTASK_CONTINUATION_MARKER + "\n\n"
                + _SUBTASK_CONTINUATION_PROMPT
            )),
        ]}
    else:
        initial_input = {"messages": [{"role": "user", "content": user_message}]}

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

    aborted = await _stream_and_translate(
        agent, input_data, config, session_id, publish, check_abort,
    )
    if aborted:
        # Phase P3: if aborted due to subtask_waiting, don't reset to idle —
        # the session should stay in subtask_waiting until child completes.
        if await _is_subtask_waiting(session_id):
            # Persist messages accumulated so far, then exit cleanly
            snapshot = await agent.aget_state(config)
            if snapshot:
                messages = snapshot.values.get("messages", [])
                if messages:
                    await _persist_messages(session_id, messages)
            await publish(session_id, {"event": "done", "reason": "subtask_waiting"})
            return
        await _update_db_status(session_id, "idle")
        await publish(session_id, {"event": "status_change", "status": "idle"})
        return

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
    check_abort: Optional[AbortCheckFn] = None,
) -> bool:
    """Stream agent events and translate to AgentD SSE events.

    Uses dual stream mode for token-level text streaming:
    - "messages": yields AIMessageChunk per token → text_delta (requires streaming=True)
    - "updates": yields complete node outputs → tool_start / tool_result

    Phase L: Also incrementally persists tool_call and tool_result messages
    to the messages table as they occur, rather than waiting for _finalize().
    Phase L: Checks abort at node boundaries (after each model/tools node).

    Returns True if aborted mid-stream, False otherwise.
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
                    # Phase P4-A: reset per-turn result accumulator for the new model turn
                    from tools.registry import reset_turn_accumulator
                    reset_turn_accumulator(session_id)
                    # Complete (aggregated) model output from "updates" channel.
                    # When streaming=True, text was already sent token-by-token
                    # via "messages". When streaming=False, no "messages" events
                    # are emitted, so we must emit text_delta from here.
                    messages = node_data.get("messages", [])
                    for msg in messages:
                        # Emit text content for non-streaming models
                        # (AIMessage, not AIMessageChunk = non-streaming output)
                        if (
                            isinstance(msg, AIMessage)
                            and not isinstance(msg, AIMessageChunk)
                            and msg.content
                        ):
                            if current_message_id is None:
                                current_message_id = str(uuid.uuid4())
                            cleaned, reasoning_delta = think_filter.feed(msg.content)
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
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                await publish(session_id, {
                                    "event": "tool_start",
                                    "tool_call_id": tc.get("id", ""),
                                    "tool_name": tc["name"],
                                    "input": tc.get("args", {}),
                                })
                            # Phase L: incrementally persist AIMessage with tool_calls
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
                    # Phase L: incrementally persist all ToolMessages from this node
                    for msg in messages:
                        if isinstance(msg, ToolMessage):
                            await _persist_message_incremental(session_id, msg)

            # Phase L: check abort at node boundaries (after each updates batch)
            if check_abort and await check_abort():
                return True

            # Phase P3: if launch_subagent set parent to subtask_waiting,
            # stop the current run immediately so we don't continue looping.
            if await _is_subtask_waiting(session_id):
                return True

    # Flush any remaining buffered text after the stream ends
    remaining = think_filter.flush()
    if remaining and current_message_id:
        await publish(session_id, {
            "event": "text_delta",
            "message_id": current_message_id,
            "content": remaining,
        })

    return False


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

            aborted = await _stream_and_translate(
                agent, resume_payload, config, session_id, publish, check_abort,
            )

            # Check abort boundary after auto-resume (also caught mid-stream by L4)
            if aborted or (check_abort and await check_abort()):
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
    # Phase L: persist all in-flight messages before entering waiting state,
    # so that session switches / refreshes can recover full tool history.
    full_state = snapshot.values if snapshot else {}
    waiting_messages = full_state.get("messages", [])
    if waiting_messages:
        await _persist_messages(session_id, waiting_messages)

    # Phase L fix: record diagnostics before entering waiting state,
    # so that waiting runs also have prompt continuity evidence.
    await _record_run_diagnostics(agent, session_id, waiting_messages)

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

    # Phase L: record prompt diagnostics on the active run
    await _record_run_diagnostics(agent, session_id, messages)

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
        await _record_run_diagnostics(agent, session_id, messages)
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

    # Auto-generate title (best-effort, non-blocking)
    asyncio.create_task(_maybe_generate_title(session_id, messages, publish))

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


async def _record_run_diagnostics(agent, session_id: str, messages: list) -> None:
    """Write prompt diagnostics to the active agent_run record (Phase L).

    Combines prompt layer sizes (from build_system_prompt) with message
    history statistics and per-run token counts so that each run has a
    complete diagnostic snapshot.

    Phase L prompt strategy: called early in _execute_graph (with empty messages)
    to ensure prompt diagnostics survive even on failed runs. Called again at
    _finalize/_handle_interrupt with full messages for complete diagnostics.
    """
    try:
        from agent import scheduler

        prompt_diag = getattr(agent, "_prompt_diagnostics", {})

        # Count messages by type
        ai_count = sum(1 for m in messages if isinstance(m, AIMessage))
        tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
        human_count = sum(1 for m in messages if isinstance(m, HumanMessage))
        system_count = sum(1 for m in messages if isinstance(m, SystemMessage))

        # Run-level accumulated token counts (across all model calls in run)
        token_usage = _extract_token_usage(messages)
        # Call-level: exact token counts from the LAST provider call
        last_call = _extract_last_call_usage(messages)

        # Phase L §12.6: checkpoint composition breakdown — lets us distinguish
        # info loss from ordering instability when diagnosing prompt cliffs.
        checkpoint_composition = {
            "human": human_count,
            "ai": ai_count,
            "tool": tool_count,
            "system": system_count,
            "total": len(messages),
        }

        # Phase M3: skill observability — extract skill loads and plan ordering
        import re as _re
        _skill_re = _re.compile(r"^\[Skill: (.+?) v(.+?)\]")
        active_skill_names: list[str] = []
        _skill_seen: set[str] = set()
        last_skill_load_idx: int = -1
        first_plan_idx: int = -1
        for idx, m in enumerate(messages):
            if isinstance(m, ToolMessage) and m.content:
                sm = _skill_re.match(m.content)
                if sm:
                    sname = sm.group(1)
                    if sname not in _skill_seen:
                        _skill_seen.add(sname)
                        active_skill_names.append(sname)
                    last_skill_load_idx = idx
                elif getattr(m, "name", "") == "planning" and first_plan_idx < 0:
                    first_plan_idx = idx

        diagnostics = {
            **prompt_diag,
            "history_message_count": len(messages),
            "history_ai_count": ai_count,
            "history_tool_count": tool_count,
            "history_human_count": human_count,
            # Run-level accumulated (for trend analysis)
            "prompt_tokens": token_usage["input"],
            "completion_tokens": token_usage["output"],
            "total_tokens": token_usage["total"],
            # Call-level precise (for window occupancy & compaction)
            "last_call_prompt_tokens": last_call["prompt_tokens"],
            "last_call_completion_tokens": last_call["completion_tokens"],
            "last_call_total_tokens": last_call["total_tokens"],
            "last_call_cache_read_tokens": last_call["cache_read_tokens"],
            "last_call_cache_creation_tokens": last_call["cache_creation_tokens"],
            "checkpoint_composition": checkpoint_composition,
            # Phase M3: skill execution observability
            "skill_loads_this_run": len(active_skill_names),
            "active_skill_names": active_skill_names,
            "plan_after_skill_load": (
                first_plan_idx > last_skill_load_idx
                if last_skill_load_idx >= 0 and first_plan_idx >= 0
                else None
            ),
            # Phase P4-B: microcompact observability
            **_get_microcompact_diagnostics(agent),
            # Phase P4-D: compaction mode
            **_get_compaction_mode_diagnostics(getattr(agent, "_session_dir", None)),
        }

        async with AsyncSessionLocal() as db:
            run = await scheduler.get_active_run(db, uuid.UUID(session_id))
            if run:
                # Phase L §12.4: attach run_type for start/resume/abort distinction
                diagnostics["run_type"] = run.run_type
                # Phase L §12.5: context window ratio — uses CALL-LEVEL prompt
                # tokens for accurate current-window occupancy display.
                # Always write context_window_limit; ratio only when tokens available.
                context_window_limit = getattr(agent, "_context_window_limit", None)
                if context_window_limit:
                    diagnostics["context_window_limit"] = context_window_limit
                    if last_call["prompt_tokens"] > 0:
                        diagnostics["context_usage_ratio"] = round(
                            last_call["prompt_tokens"] / context_window_limit, 4,
                        )
                await scheduler.update_diagnostics(db, run.id, diagnostics)
                await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


def _extract_knowledge_source_refs(messages: list) -> list[dict]:
    """Phase P6-D: extract knowledge source references from tool results.

    Scans ToolMessages from knowledge_search and knowledge_read,
    extracts doc_id / title / kind / source_file / evidence_excerpt
    to be attached as a source_refs part on the final assistant message.
    """
    import json as _json

    sources: dict[str, dict] = {}  # keyed by doc_id for dedup

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = getattr(msg, "name", "") or ""
        if tool_name not in ("knowledge_search", "knowledge_read"):
            continue

        content = msg.content if isinstance(msg.content, str) else ""
        try:
            data = _json.loads(content)
        except (ValueError, _json.JSONDecodeError):
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
                # Update with richer data from read
                if data.get("title"):
                    entry["title"] = data["title"]
                if data.get("source_file"):
                    entry["source_file"] = data["source_file"]
                if evidence and not entry.get("evidence_excerpt"):
                    entry["evidence_excerpt"] = evidence
                sources[doc_id] = entry

    # Assign ref_index (1-based) for [1] [2] citation alignment
    result = list(sources.values())
    for i, src in enumerate(result):
        src["ref_index"] = i + 1
    return result


def _get_compaction_mode_diagnostics(session_dir: str | None) -> dict:
    """Extract compaction mode from session_memory_meta.json for diagnostics."""
    if not session_dir:
        return {"compaction_mode": "pre_hard_compact"}
    try:
        from agent.session_memory import read_meta
        meta = read_meta(session_dir)
        return {
            "compaction_mode": "post_hard_compact" if meta.get("post_hard_compact") else "pre_hard_compact",
            "memory_available": meta.get("memory_valid", False),
            "memory_snapshot_version": meta.get("snapshot_version", 0),
            "memory_token_estimate": meta.get("memory_token_estimate", 0),
        }
    except Exception:
        return {"compaction_mode": "pre_hard_compact"}


def _get_microcompact_diagnostics(agent) -> dict:
    """Extract microcompact result from agent metadata for diagnostics."""
    mc = getattr(agent, "_microcompact_result", None)
    if not mc:
        return {"microcompact_applied": False}
    return {
        "microcompact_applied": mc.get("applied", False),
        "microcompact_removed_count": mc.get("removed_count", 0),
        "microcompact_replaced_count": mc.get("replaced_count", 0),
        "microcompact_reason": mc.get("reason", ""),
    }


# ── Helpers (moved from runner.py) ───────────────────────────────────────


def _is_tool_error(msg) -> bool:
    if getattr(msg, "status", "") == "error":
        return True
    additional = getattr(msg, "additional_kwargs", {})
    if additional.get("is_error"):
        return True
    return False


def _extract_token_usage(messages: list) -> dict:
    """Run-level accumulated token counts across all model calls."""
    total_input = 0
    total_output = 0
    for msg in messages:
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if usage and isinstance(usage, dict):
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
    return {"input": total_input, "output": total_output, "total": total_input + total_output}


def _extract_last_call_usage(messages: list) -> dict:
    """Call-level: exact token counts from the most recent provider call.

    Reads usage_metadata from the LAST AIMessage in the checkpoint.
    This is the precise prompt token count for the current model invocation,
    suitable for context window occupancy display and compaction triggers.
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if usage and isinstance(usage, dict):
                return {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": (
                        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    ),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
                }
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


async def _persist_message_incremental(session_id: str, msg) -> None:
    """Persist a single AIMessage or ToolMessage to the messages table immediately.

    Phase L: called from _stream_and_translate() at tool boundaries so that
    tool_call / tool_result history survives session switches and refreshes.
    Uses its own DB session + commit for isolation from the main flow.
    """
    try:
        async with AsyncSessionLocal() as db:
            sid = uuid.UUID(session_id)

            if isinstance(msg, AIMessage):
                parts = []
                if msg.content:
                    raw = msg.content
                    clean = _strip_model_tags(raw)
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

            await db.commit()
    except Exception:
        if settings.debug:
            traceback.print_exc()


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
                if isinstance(msg, AIMessage) and \
                   getattr(msg, "additional_kwargs", {}).get("agentd_internal") == _SUBTASK_RESULT_BRIDGE_KIND:
                    continue
                if isinstance(msg, HumanMessage) and \
                   _SUBTASK_CONTINUATION_MARKER in (msg.content or ""):
                    continue
                persistable.append(msg)

            skip = max(existing_count - 1, 0)
            new_messages = persistable[skip:]

            # Phase N1: post-compaction recovery.
            # After checkpoint rewrite, checkpoint has fewer messages than DB
            # (DB keeps full history, checkpoint is compacted). The count-based
            # skip overshoots and drops the new assistant response.
            # Fallback: find the last real user message and persist everything after it.
            if not new_messages and len(persistable) > 0:
                last_user_idx = -1
                for j in range(len(persistable) - 1, -1, -1):
                    if isinstance(persistable[j], HumanMessage) and \
                       "[Context Summary]" not in (persistable[j].content or "") and \
                       _SUBTASK_CONTINUATION_MARKER not in (persistable[j].content or ""):
                        last_user_idx = j
                        break
                if last_user_idx >= 0:
                    new_messages = [
                        m for m in persistable[last_user_idx + 1:]
                        if isinstance(m, (AIMessage, ToolMessage))
                    ]

            # Phase P6-D: collect knowledge source refs from ALL messages in this run
            # (not just new_messages, because ToolMessages are often already persisted
            # incrementally and won't appear in new_messages at finalize time)
            knowledge_source_refs = _extract_knowledge_source_refs(messages)

            for i, msg in enumerate(new_messages):
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
                    # Phase P6-D: attach source_refs to the LAST assistant message
                    is_last_ai = not any(
                        isinstance(m, AIMessage) for m in new_messages[i + 1:]
                    )
                    if is_last_ai and knowledge_source_refs:
                        parts.append({
                            "type": "source_refs",
                            "sources": knowledge_source_refs,
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
