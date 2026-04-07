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
    from langchain_core.messages import RemoveMessage

    # 1. Get current checkpoint state
    snapshot = await agent.aget_state(config)
    if not snapshot:
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="no_snapshot")

    messages = snapshot.values.get("messages", [])
    if len(messages) < 5:
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="too_few_messages")

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

    # 4. Execute three-action classification
    remove_ids = []          # Action 1: remove entirely
    replace_messages = []    # Action 2: replace with preview+ref
    capsule_messages = []    # Action 3: compress to capsule
    remove_count = 0
    replace_count = 0
    capsule_count = 0

    for candidate in actionable:
        idx = candidate["index"]
        msg = messages[idx]
        tool_name = candidate["tool_name"]
        chars = candidate["chars"]

        if not hasattr(msg, "id") or not msg.id:
            continue

        content = msg.content if isinstance(msg.content, str) else str(msg.content)

        is_high_compress = candidate.get("is_high_compress", False)
        is_preview = candidate.get("is_preview", False)

        if chars > SINGLE_MSG_SIZE_THRESHOLD or is_preview:
            # Action 2: Large result or P4-A preview → replace with preview + ref
            preview = content[:PREVIEW_CHARS]
            replacement = ToolMessage(
                content=(
                    f"{preview}\n\n"
                    f"--- [Microcompact: result truncated from {chars:,} chars. "
                    f"Full output in artifact if saved by P4-A.] ---"
                ),
                name=tool_name,
                tool_call_id=getattr(msg, "tool_call_id", ""),
                id=msg.id,
            )
            remove_ids.append(msg.id)
            replace_messages.append(replacement)
            replace_count += 1
        elif is_high_compress and chars > 100:
            # Action 3: Medium/short high-compress tool result → capsule
            capsule_text = f"[{tool_name} result: {chars:,} chars, removed by microcompact]"
            capsule = ToolMessage(
                content=capsule_text,
                name=tool_name,
                tool_call_id=getattr(msg, "tool_call_id", ""),
                id=msg.id,
            )
            remove_ids.append(msg.id)
            capsule_messages.append(capsule)
            capsule_count += 1
        else:
            # Action 1: Small old result → remove entirely
            remove_ids.append(msg.id)
            remove_count += 1

    if not remove_ids:
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="no_removable_ids")

    # 5. Apply via checkpoint update: remove old + add replacements/capsules
    try:
        update_messages = [RemoveMessage(id=rid) for rid in remove_ids]
        # Add back preview+ref replacements and capsules
        update_messages.extend(replace_messages)
        update_messages.extend(capsule_messages)
        await agent.aupdate_state(
            config=config,
            values={"messages": update_messages},
        )
    except Exception as e:
        logger.warning("Microcompact checkpoint update failed: %s", e)
        return MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason=f"error: {e}")

    logger.info(
        "Microcompact session=%s: removed=%d replaced=%d capsules=%d reason=%s",
        session_id[:8] if session_id else "?", remove_count, replace_count, capsule_count, reason,
    )

    return MicrocompactResult(
        applied=True,
        removed_count=remove_count,
        replaced_count=replace_count + capsule_count,
        reason=reason,
    )


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
