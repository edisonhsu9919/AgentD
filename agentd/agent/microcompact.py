"""Microcompact — per-turn lightweight context trimming (Phase P4-B).

Runs before each model call to keep the prompt within manageable size.
Unlike hard compact, microcompact does NOT generate summaries or call LLM.
It only:
  1. Removes old low-value tool results from the checkpoint
  2. Replaces large results with preview + artifact ref
  3. Compresses intermediate narration into capsules

Protected items are never touched:
  - Last 2 complete turns (user + assistant + tool results)
  - Latest planning / todo_update results
  - subtask_result messages
  - Active tool chain (incomplete)
  - mutates_session_state=true + result_compressibility=low results
"""

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

# Trigger thresholds
RATIO_THRESHOLD = 0.6           # context_usage_ratio above this → scan
MIN_COMPRESSIBLE_COUNT = 8      # minimum compressible messages before acting
SINGLE_MSG_SIZE_THRESHOLD = 30_000  # single message chars above this → always compress

# Protection
FRONTIER_TURNS = 2              # keep last N complete user→assistant turns
PREVIEW_CHARS = 2000            # preview size for replaced results

# Tool names whose latest result is always protected
PROTECTED_TOOL_NAMES = {"planning", "todo_update", "skill"}

# Tools with high compressibility — prioritize these for removal
HIGH_COMPRESS_TOOLS = {"grep", "list_dir", "glob", "bash", "script", "file_inspect"}


@dataclass
class MicrocompactResult:
    """Result of a microcompact pass."""
    applied: bool
    removed_count: int
    replaced_count: int
    reason: str


async def run_microcompact(
    agent,
    config: dict,
    session_id: str,
    context_usage_ratio: float | None = None,
) -> MicrocompactResult:
    """Execute a microcompact pass on the current checkpoint.

    Scans messages for compressible tool results and removes/replaces
    them to control prompt size. Does NOT call any LLM.

    Args:
        agent: Compiled LangGraph agent (for aget_state / aupdate_state)
        config: LangGraph config with thread_id
        session_id: For logging
        context_usage_ratio: Current ratio if known (skips threshold check if None)

    Returns MicrocompactResult with counts of what was done.
    """
    # 1. Get current checkpoint state
    snapshot = await agent.aget_state(config)
    if not snapshot:
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="no_snapshot")

    messages = snapshot.values.get("messages", [])
    if len(messages) < 5:
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="too_few_messages")
    if not _checkpoint_tool_adjacency_is_valid(messages):
        logger.warning(
            "Microcompact skipped invalid checkpoint: session=%s bad=%s",
            session_id[:8] if session_id else "?",
            _find_invalid_tool_adjacency_indices(messages),
        )
        return MicrocompactResult(
            applied=False,
            removed_count=0,
            replaced_count=0,
            reason="invalid_checkpoint",
        )

    # 2. Identify candidates and protected messages
    candidates = _find_compressible_candidates(messages)
    protected = _find_protected_indices(messages)

    # Filter out protected
    actionable = [c for c in candidates if c["index"] not in protected]

    # Always log scan results for live debugging
    logger.info(
        "Microcompact scan: messages=%d candidates=%d protected_count=%d actionable=%d ratio=%s",
        len(messages), len(candidates), len(protected), len(actionable),
        context_usage_ratio,
    )

    if not actionable:
        return MicrocompactResult(
            applied=False, removed_count=0, replaced_count=0,
            reason=f"nothing_actionable(msgs={len(messages)},cand={len(candidates)},prot={len(protected)})",
        )

    # 3. Check trigger thresholds
    # Fix: count actionable by compressible tool results, not post-char-filter count
    should_act = False
    reason = ""

    # Condition 1: high context usage (backstop)
    if context_usage_ratio is not None and context_usage_ratio > RATIO_THRESHOLD:
        should_act = True
        reason = f"ratio={context_usage_ratio:.2f}"

    # Condition 2: enough compressible tool results (primary trigger)
    if len(actionable) >= MIN_COMPRESSIBLE_COUNT:
        should_act = True
        reason = reason or f"compressible_count={len(actionable)}"

    # Condition 3: any oversized result OR any P4-A preview result
    oversized = [c for c in actionable if c["chars"] > SINGLE_MSG_SIZE_THRESHOLD]
    previewed = [c for c in actionable if c.get("is_preview")]
    if oversized:
        should_act = True
        reason = reason or f"oversized_count={len(oversized)}"
    if previewed:
        should_act = True
        reason = reason or f"previewed_count={len(previewed)}"

    # Condition 4: any high-compress tool results outside frontier (even if few)
    # This catches the common case of accumulated short noise results
    high_compress_actionable = [c for c in actionable if c.get("is_high_compress")]
    if len(high_compress_actionable) >= 3:
        should_act = True
        reason = reason or f"high_compress_count={len(high_compress_actionable)}"

    if not should_act:
        return MicrocompactResult(
            applied=False, removed_count=0, replaced_count=0,
            reason=(
                f"below_threshold(actionable={len(actionable)}"
                f",high={len(high_compress_actionable)}"
                f",preview={len(previewed)}"
                f",oversized={len(oversized)}"
                f",ratio={context_usage_ratio})"
            ),
        )

    # 4. Build a replacement-only checkpoint.
    # OpenAI-compatible providers require every assistant tool_call to be
    # followed by its ToolMessage. Therefore microcompact must preserve the
    # tool result envelope and only shrink ToolMessage content in place.
    replacements_by_index: dict[int, ToolMessage] = {}
    replace_count = 0
    capsule_count = 0

    for candidate in actionable:
        idx = candidate["index"]
        msg = messages[idx]
        tool_name = candidate["tool_name"]
        chars = candidate["chars"]

        content = msg.content if isinstance(msg.content, str) else str(msg.content)

        is_high_compress = candidate.get("is_high_compress", False)
        is_preview = candidate.get("is_preview", False)

        if chars > SINGLE_MSG_SIZE_THRESHOLD or is_preview:
            replacement = _build_compacted_tool_message(
                msg,
                tool_name=tool_name,
                original_content=content,
                original_chars=chars,
                preview_chars=PREVIEW_CHARS,
            )
            replacements_by_index[idx] = replacement
            replace_count += 1
        elif is_high_compress or chars > 100:
            capsule = _build_compacted_tool_message(
                msg,
                tool_name=tool_name,
                original_content=content,
                original_chars=chars,
                preview_chars=0,
            )
            if _content_len(capsule.content) >= chars:
                continue
            replacements_by_index[idx] = capsule
            capsule_count += 1

    if not replacements_by_index:
        return MicrocompactResult(
            applied=False,
            removed_count=0,
            replaced_count=0,
            reason="no_effective_replacements",
        )

    rebuilt_messages = [
        replacements_by_index.get(idx, msg)
        for idx, msg in enumerate(messages)
    ]
    if not _checkpoint_tool_adjacency_is_valid(rebuilt_messages):
        logger.warning(
            "Microcompact aborted invalid rebuild: session=%s bad=%s",
            session_id[:8] if session_id else "?",
            _find_invalid_tool_adjacency_indices(rebuilt_messages),
        )
        return MicrocompactResult(
            applied=False,
            removed_count=0,
            replaced_count=0,
            reason="invalid_rebuild",
        )

    # 5. Apply via full checkpoint rewrite. Avoid RemoveMessage(id=tool_id)
    # followed by a same-id ToolMessage because LangGraph remove semantics can
    # swallow the replacement and leave dangling assistant tool_calls.
    try:
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        new_config = await _aupdate_microcompact_checkpoint(
            agent,
            config,
            [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *rebuilt_messages,
            ],
        )
        _merge_updated_config(config, new_config)
    except Exception as e:
        logger.warning("Microcompact checkpoint update failed: %s", e)
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason=f"error: {e}")

    try:
        updated_snapshot = await agent.aget_state(config)
        updated_messages = (
            updated_snapshot.values.get("messages", [])
            if updated_snapshot else []
        )
        if not _checkpoint_tool_adjacency_is_valid(updated_messages):
            logger.warning(
                "Microcompact produced invalid checkpoint: session=%s bad=%s",
                session_id[:8] if session_id else "?",
                _find_invalid_tool_adjacency_indices(updated_messages),
            )
            return MicrocompactResult(
                applied=False,
                removed_count=0,
                replaced_count=0,
                reason="invalid_after_update",
            )
    except Exception as e:
        logger.warning("Microcompact checkpoint verification failed: %s", e)
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason=f"verify_error: {e}")

    logger.info(
        "Microcompact session=%s: removed=%d replaced=%d capsules=%d reason=%s",
        session_id[:8] if session_id else "?", 0, replace_count, capsule_count, reason,
    )

    return MicrocompactResult(
        applied=True,
        removed_count=0,
        replaced_count=replace_count + capsule_count,
        reason=reason,
    )


def _content_len(content: Any) -> int:
    return len(content if isinstance(content, str) else str(content))


def _build_compacted_tool_message(
    msg: ToolMessage,
    tool_name: str,
    original_content: str,
    original_chars: int,
    preview_chars: int,
) -> ToolMessage:
    tool_name = tool_name or getattr(msg, "name", "") or "unknown"
    tool_call_id = getattr(msg, "tool_call_id", "") or ""
    content = _build_compacted_tool_content(
        tool_name=tool_name,
        original_content=original_content,
        original_chars=original_chars,
        preview_chars=preview_chars,
    )
    kwargs = {
        "content": content,
        "name": tool_name,
        "tool_call_id": tool_call_id,
        "id": getattr(msg, "id", None),
    }
    status = getattr(msg, "status", None)
    if status:
        kwargs["status"] = status
    artifact = getattr(msg, "artifact", None)
    if artifact is not None:
        kwargs["artifact"] = artifact
    return ToolMessage(**kwargs)


def _build_compacted_tool_content(
    tool_name: str,
    original_content: str,
    original_chars: int,
    preview_chars: int,
) -> str:
    lines = [
        "[Tool result compacted by AgentD microcompact.",
        f"Tool: {tool_name}",
        f"Original size: {original_chars:,} chars",
    ]
    if preview_chars > 0:
        preview = original_content[:preview_chars]
        lines.extend([
            "Preview:",
            preview,
            "Full output omitted to save context.]",
        ])
    else:
        lines.append("Full output omitted to save context.]")
    return "\n".join(lines)


def _merge_updated_config(config: dict, new_config: Any) -> None:
    if not isinstance(new_config, dict):
        return
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
    if not thread_id and not checkpoint_ns:
        return
    config.clear()
    config["configurable"] = {}
    if thread_id:
        config["configurable"]["thread_id"] = thread_id
    if checkpoint_ns:
        config["configurable"]["checkpoint_ns"] = checkpoint_ns


async def _aupdate_microcompact_checkpoint(agent, config: dict, messages: list):
    """Rewrite checkpoint messages from an explicit maintenance boundary.

    Real LangGraph graphs cannot always infer the node that owns a full
    checkpoint rewrite, so microcompact must pass as_node. Test doubles and
    older adapters may not accept that keyword, hence the TypeError fallback.
    """
    values = {"messages": messages}
    try:
        return await agent.aupdate_state(
            config=config,
            values=values,
            as_node="__start__",
        )
    except TypeError:
        return await agent.aupdate_state(config=config, values=values)


def _find_compressible_candidates(messages: list) -> list[dict]:
    """Identify tool result messages that are candidates for compression.

    Returns list of {"index": int, "tool_name": str, "chars": int, "is_preview": bool}.
    Only ToolMessage instances are candidates.

    Phase P4-B fix: high-compressibility tools enter candidates regardless of
    char count (even short "No matches found" results). Only non-high-compress
    tools require chars >= 200 to enter.

    P4-A preview/ref results are explicitly marked so P4-B can still act on them.
    """
    candidates = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            continue

        tool_name = getattr(msg, "name", "") or ""
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        chars = len(content)

        # Detect P4-A preview/ref results
        is_preview = "Result truncated:" in content and ".agentd/artifacts/" in content

        is_high_compress = tool_name in HIGH_COMPRESS_TOOLS

        # High compressibility tools: always enter candidates (even empty/short)
        # Other tools: only if chars >= 200
        # P4-A preview results: always enter candidates
        if not is_high_compress and not is_preview and chars < 200:
            continue

        candidates.append({
            "index": i,
            "tool_name": tool_name,
            "chars": chars,
            "msg_id": getattr(msg, "id", None),
            "is_preview": is_preview,
            "is_high_compress": is_high_compress,
        })

    return candidates


def _find_protected_indices(messages: list) -> set[int]:
    """Determine which message indices must NOT be touched.

    Phase P4-D aligned protection set (1-7):
    1. Latest planning result
    2. Latest todo_update result
    3. Latest subtask_result
    4. Current waiting boundary (permission/subtask)
    5. Active skill current state (latest skill load result)
    6. Running detached tasks summary (latest launch_detached_process result)
    7. mutates_session_state=true && result_compressibility=low tool results

    Plus structural protections:
    - System message
    - Last FRONTIER_TURNS complete turns
    - Context summary messages
    """
    protected = set()

    # Structural: system message
    if messages and isinstance(messages[0], SystemMessage):
        protected.add(0)

    # Structural: last N complete turns from the end
    turns_found = 0
    i = len(messages) - 1
    while i >= 0 and turns_found < FRONTIER_TURNS:
        protected.add(i)
        if isinstance(messages[i], HumanMessage):
            turns_found += 1
        i -= 1

    # Protection 1-2: latest planning and todo_update
    # Protection 5: latest skill load
    # Protection 6: latest launch_detached_process result
    # Protection 7: mutates_session_state=true tools (planning/todo_update/skill)
    # All covered by tracking latest result from protected tool set
    protected_latest: dict[str, int] = {}
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "") or ""
            if tool_name in PROTECTED_TOOL_NAMES or tool_name == "launch_detached_process":
                protected_latest[tool_name] = i

    protected.update(protected_latest.values())

    # Protection 3: subtask_result messages (assistant messages with special part)
    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            if "[Sub-task completed]" in content or "[Subtask Result" in content:
                protected.add(i)

    # Protection 4: waiting boundary — protect messages around permission/subtask waits
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            # Permission waiting results
            if "waiting_for_child" in content or "permission" in content.lower():
                protected.add(i)

    # Structural: context summary messages
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else ""
            if "[Context Summary]" in content:
                protected.add(i)

    # Protection 7: LATEST result from mutates_session_state=true tools
    # Only the most recent result from each is protected, not every historical one.
    _session_state_tools = {"planning", "todo_update", "skill", "launch_subagent"}
    _latest_state_tool: dict[str, int] = {}
    for i, msg in enumerate(messages):
        if isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "") or ""
            if tool_name in _session_state_tools:
                _latest_state_tool[tool_name] = i
    protected.update(_latest_state_tool.values())

    return protected


def _checkpoint_tool_adjacency_is_valid(messages: list) -> bool:
    return not _find_invalid_tool_adjacency_indices(messages)


def _find_invalid_tool_adjacency_indices(messages: list) -> list[int]:
    invalid: set[int] = set()
    i = 0
    while i < len(messages):
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            required_ids = [
                tc.get("id")
                for tc in getattr(msg, "tool_calls", []) or []
                if tc.get("id")
            ]
            j = i + 1
            tool_indices: list[int] = []
            tool_ids: list[str | None] = []
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                tool_indices.append(j)
                tool_ids.append(getattr(messages[j], "tool_call_id", None))
                j += 1

            if (
                len(tool_ids) < len(required_ids)
                or any(tool_call_id not in tool_ids for tool_call_id in required_ids)
                or any(tool_call_id not in required_ids for tool_call_id in tool_ids)
            ):
                invalid.add(i)
                invalid.update(tool_indices)
            i = max(j, i + 1)
            continue

        if isinstance(msg, ToolMessage):
            invalid.add(i)

        i += 1

    return sorted(invalid)
