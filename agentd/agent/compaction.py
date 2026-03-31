"""AgentD-native context compaction (Phase N1).

Replaces the black-box SummarizationMiddleware with explicit,
protection-aware context compaction.

Core flow:
1. Read current checkpoint messages
2. Classify messages into protected / compactable / frontier
3. Generate structured summary of compactable messages via LLM
4. Write DB summary message (is_summary=True)
5. Write context_summary.json artifact
6. Rewrite checkpoint state with [summary + protected + frontier]
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

from core.config import settings
from core.database import AsyncSessionLocal
from session import service as session_svc

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AUTO_TRIGGER_RATIO = 0.85   # automatic compaction threshold
WARNING_RATIO = 0.70        # SSE context_warning threshold
FRONTIER_KEEP = 20          # messages to preserve in frontier window
MIN_COMPACTABLE = 6         # don't compact if fewer messages are eligible

_SUMMARY_PROMPT_PATH = Path(__file__).parent / "prompts" / "hidden" / "context_summary.md"

# Required keys in the structured summary JSON
_SUMMARY_SCHEMA_KEYS = {
    "session_intent": str,
    "key_decisions": list,
    "current_task_state": str,
    "active_skill": (str, type(None)),
    "important_artifacts": list,
    "conversation_highlights": list,
    "next_steps": list,
}

# ── Protection classification ────────────────────────────────────────────────

_SKILL_LOAD_RE = re.compile(r"^\[Skill: .+? v.+?\]")


def classify_messages(
    messages: list,
) -> tuple[list, list[int], list[int], list[int]]:
    """Partition checkpoint messages into system / protected / compactable / frontier.

    Returns:
        (all_messages, protected_indices, compactable_indices, frontier_indices)

    Protection rules:
    - SystemMessage at index 0 is always kept as-is (not part of compaction)
    - Active skill full text (ToolMessage matching [Skill: ...]) — protected
      if it is the LAST skill load in the conversation (active workflow)
    - Latest planning / todo_update ToolMessages — protected
    - Unresolved tool_call/tool_result pairs at the tail — protected
    - Frontier window (last FRONTIER_KEEP messages) — protected

    Everything else in the middle is compactable.
    """
    if not messages:
        return messages, [], [], []

    n = len(messages)

    # Index 0 is always SystemMessage — skip it
    start = 1 if isinstance(messages[0], SystemMessage) else 0

    # Step 1: identify frontier window
    frontier_start = max(start, n - FRONTIER_KEEP)
    frontier_indices = list(range(frontier_start, n))

    # Step 2: identify protected messages in the non-frontier region
    protected: set[int] = set()

    # 2a: Find the LAST skill load — it's the active workflow
    last_skill_idx = -1
    for i in range(n - 1, start - 1, -1):
        msg = messages[i]
        if isinstance(msg, ToolMessage) and msg.content and _SKILL_LOAD_RE.match(msg.content):
            last_skill_idx = i
            break
    if last_skill_idx >= 0 and last_skill_idx < frontier_start:
        protected.add(last_skill_idx)

    # 2b: Find the LAST planning and LAST todo_update ToolMessages
    last_planning_idx = -1
    last_todo_idx = -1
    for i in range(n - 1, start - 1, -1):
        msg = messages[i]
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "") or ""
            if name == "planning" and last_planning_idx < 0:
                last_planning_idx = i
            elif name == "todo_update" and last_todo_idx < 0:
                last_todo_idx = i
            if last_planning_idx >= 0 and last_todo_idx >= 0:
                break
    for idx in (last_planning_idx, last_todo_idx):
        if idx >= 0 and idx < frontier_start:
            protected.add(idx)

    # 2c: Protect AI+Tool pairs that straddle the frontier boundary
    # If frontier starts mid-pair (ToolMessage without its AIMessage), pull the AI in
    if frontier_start < n and isinstance(messages[frontier_start], ToolMessage):
        # Walk backwards to find the AIMessage that issued this tool call
        for j in range(frontier_start - 1, start - 1, -1):
            protected.add(j)
            if isinstance(messages[j], AIMessage):
                break

    # 2d: For any protected ToolMessage, also protect its preceding AIMessage (the tool_call)
    extra_protect: set[int] = set()
    for idx in protected:
        if isinstance(messages[idx], ToolMessage) and idx > start:
            # Walk backwards to find the AI that triggered this tool
            for j in range(idx - 1, start - 1, -1):
                extra_protect.add(j)
                if isinstance(messages[j], AIMessage):
                    break
    protected |= extra_protect

    # Remove any protected indices that fall in the frontier (they're already kept)
    protected -= set(frontier_indices)

    protected_indices = sorted(protected)

    # Step 3: everything else in [start, frontier_start) not protected is compactable
    compactable_indices = [
        i for i in range(start, frontier_start) if i not in protected
    ]

    return messages, protected_indices, compactable_indices, frontier_indices


# ── Summary generation ───────────────────────────────────────────────────────


def _build_summary_input(messages: list, compactable_indices: list[int]) -> str:
    """Format compactable messages into text for the summary LLM."""
    parts: list[str] = []
    for i in compactable_indices:
        msg = messages[i]
        if isinstance(msg, HumanMessage):
            parts.append(f"[User]: {msg.content[:2000]}")
        elif isinstance(msg, AIMessage):
            content = msg.content[:2000] if msg.content else ""
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tools = ", ".join(tc["name"] for tc in msg.tool_calls)
                content += f" [called: {tools}]"
            if content:
                parts.append(f"[Assistant]: {content}")
        elif isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "tool")
            output = (msg.content or "")[:1500]
            parts.append(f"[Tool:{name}]: {output}")
    return "\n".join(parts)


def _validate_summary_json(text: str) -> dict | None:
    """Try to parse and validate summary text as structured JSON.

    Returns parsed dict if valid, None otherwise.
    """
    # Strip markdown code fences if model wrapped output
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ``` wrapper
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    # Check all required keys exist with correct types
    for key, expected_type in _SUMMARY_SCHEMA_KEYS.items():
        if key not in data:
            return None
        if not isinstance(data[key], expected_type):
            return None

    return data


async def generate_summary(
    messages: list,
    compactable_indices: list[int],
    model_id: str,
) -> str:
    """Generate a structured JSON summary of compactable messages.

    Flow:
    1. Call LLM with strict JSON prompt
    2. Validate response against schema
    3. On validation failure, retry once with explicit correction
    4. If still invalid, fallback to plain text marked as unstructured

    Returns the summary text (to be prefixed with [Context Summary]).
    """
    system_prompt = _SUMMARY_PROMPT_PATH.read_text(encoding="utf-8").strip()
    conversation_text = _build_summary_input(messages, compactable_indices)

    if not conversation_text.strip():
        return json.dumps({
            "session_intent": "No significant content to summarize.",
            "key_decisions": [],
            "current_task_state": "Empty",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
        })

    from model_config.service import resolve_active_model_config

    async with AsyncSessionLocal() as db:
        resolved = await resolve_active_model_config(db)

    llm = ChatOpenAI(
        model=model_id,
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        streaming=False,
        max_tokens=3000,
        http_async_client=httpx.AsyncClient(trust_env=False),
    )

    from langchain_core.messages import (
        HumanMessage as LCHumanMessage,
        SystemMessage as LCSystemMessage,
    )
    from agent.executor import _strip_model_tags

    # Attempt 1
    result = await llm.ainvoke([
        LCSystemMessage(content=system_prompt),
        LCHumanMessage(content=conversation_text),
    ])
    raw = _strip_model_tags(result.content or "").strip()
    parsed = _validate_summary_json(raw)

    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)

    # Attempt 2: retry with explicit correction
    logger.warning("Summary JSON validation failed on attempt 1, retrying with correction prompt")
    retry_prompt = (
        "Your previous response was not valid JSON. "
        "You MUST respond with ONLY a valid JSON object with these exact keys: "
        "session_intent (string), key_decisions (array), current_task_state (string), "
        "active_skill (string or null), important_artifacts (array), "
        "conversation_highlights (array), next_steps (array). "
        "No markdown, no explanation. Just the JSON object."
    )
    result2 = await llm.ainvoke([
        LCSystemMessage(content=system_prompt),
        LCHumanMessage(content=conversation_text),
        LCSystemMessage(content=retry_prompt),
    ])
    raw2 = _strip_model_tags(result2.content or "").strip()
    parsed2 = _validate_summary_json(raw2)

    if parsed2 is not None:
        return json.dumps(parsed2, ensure_ascii=False)

    # Fallback: wrap raw text as unstructured summary
    logger.warning("Summary JSON validation failed on retry, falling back to unstructured")
    fallback = {
        "session_intent": raw[:500] if raw else "Summary generation produced unstructured output.",
        "key_decisions": [],
        "current_task_state": "unknown",
        "active_skill": None,
        "important_artifacts": [],
        "conversation_highlights": [],
        "next_steps": [],
        "_unstructured": True,
        "_raw_summary": raw[:2000] if raw else "",
    }
    return json.dumps(fallback, ensure_ascii=False)


# ── Context summary JSON artifact ────────────────────────────────────────────


def _parse_summary_sections(summary_text: str) -> dict[str, Any]:
    """Parse the structured summary into sections for context_summary.json.

    Accepts JSON string (preferred) or legacy Markdown heading format (fallback).
    Returns dict with the 6 standard keys.
    """
    defaults: dict[str, Any] = {
        "session_intent": "",
        "key_decisions": [],
        "current_task_state": "",
        "active_skill": None,
        "important_artifacts": [],
        "conversation_highlights": [],
        "next_steps": [],
    }

    # Try JSON first (new strict format)
    try:
        data = json.loads(summary_text)
        if isinstance(data, dict):
            for key in defaults:
                if key in data:
                    defaults[key] = data[key]
            # Carry forward unstructured marker if present
            if data.get("_unstructured"):
                defaults["_unstructured"] = True
            return defaults
    except (json.JSONDecodeError, ValueError):
        pass

    # Legacy fallback: Markdown heading parser
    heading_map = {
        "SESSION INTENT": "session_intent",
        "KEY DECISIONS": "key_decisions",
        "CURRENT TASK STATE": "current_task_state",
        "ACTIVE SKILL": "active_skill",
        "IMPORTANT ARTIFACTS": "important_artifacts",
        "NEXT STEPS": "next_steps",
    }
    current_key = None
    current_lines: list[str] = []

    for line in summary_text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped in heading_map:
            if current_key:
                defaults[current_key] = "\n".join(current_lines).strip()
            current_key = heading_map[stripped]
            current_lines = []
        elif current_key:
            current_lines.append(line)

    if current_key:
        defaults[current_key] = "\n".join(current_lines).strip()

    return defaults


def write_context_summary_json(
    session_dir: str,
    summary_text: str,
    compacted_through_seq: int,
) -> str:
    """Write structured context_summary.json to session_dir/.agentd/.

    Returns the path to the written file.
    """
    # Read existing count to increment
    existing_count = 0
    existing_path = os.path.join(session_dir, ".agentd", "context_summary.json")
    if os.path.isfile(existing_path):
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_count = old.get("compaction_count", 0)
        except Exception:
            pass

    sections = _parse_summary_sections(summary_text)
    is_structured = not sections.pop("_unstructured", False)
    data = {
        "version": 2,
        "structured": is_structured,
        "compacted_at": datetime.now(timezone.utc).isoformat(),
        "compacted_through_seq": compacted_through_seq,
        "compaction_count": existing_count + 1,
        **sections,
    }

    dir_path = os.path.join(session_dir, ".agentd")
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, "context_summary.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)
    return path


# ── Checkpoint rewrite ───────────────────────────────────────────────────────


async def rewrite_checkpoint(
    agent,
    config: dict,
    messages: list,
    summary_text: str,
    protected_indices: list[int],
    frontier_indices: list[int],
) -> list:
    """Rewrite checkpoint state: [system + summary + protected + frontier].

    Uses LangGraph's aupdate_state with RemoveMessage to replace the
    compacted messages with a single summary HumanMessage.

    Returns the new message list for verification.
    """
    from langchain_core.messages import RemoveMessage

    # Build the set of message IDs to keep
    keep_indices = set()
    # Always keep SystemMessage at index 0
    if messages and isinstance(messages[0], SystemMessage):
        keep_indices.add(0)
    keep_indices.update(protected_indices)
    keep_indices.update(frontier_indices)

    # Messages to remove = everything not in keep set
    remove_ids = []
    for i, msg in enumerate(messages):
        if i not in keep_indices and hasattr(msg, "id") and msg.id:
            remove_ids.append(msg.id)

    if not remove_ids:
        return messages

    # Create summary HumanMessage
    summary_msg = HumanMessage(
        content=f"[Context Summary]\n{summary_text}",
        id=str(uuid.uuid4()),
    )

    # Build update: remove old messages + add summary
    update_messages = [RemoveMessage(id=rid) for rid in remove_ids]
    update_messages.append(summary_msg)

    await agent.aupdate_state(
        config=config,
        values={"messages": update_messages},
    )

    # Verify: get new state
    snapshot = await agent.aget_state(config)
    new_messages = (snapshot.values or {}).get("messages", [])
    return new_messages


# ── Main compaction orchestrator ─────────────────────────────────────────────


def should_compact(context_usage_ratio: float | None) -> bool:
    """Check if automatic compaction should trigger."""
    if context_usage_ratio is None:
        return False
    return context_usage_ratio >= AUTO_TRIGGER_RATIO


def should_warn(context_usage_ratio: float | None) -> bool:
    """Check if context_warning SSE should fire."""
    if context_usage_ratio is None:
        return False
    return context_usage_ratio >= WARNING_RATIO


async def compact_session(
    agent,
    config: dict,
    session_id: str,
    session_dir: str,
    model_id: str,
    publish=None,
) -> dict[str, Any]:
    """Execute a full compaction cycle for a session.

    Steps:
    1. Read checkpoint messages
    2. Classify into protected / compactable / frontier
    3. Generate structured summary
    4. Persist DB summary message
    5. Write context_summary.json
    6. Rewrite checkpoint
    7. Publish SSE compaction_done

    Returns diagnostics dict with compaction stats.
    """
    # 1. Read current checkpoint
    snapshot = await agent.aget_state(config)
    messages = (snapshot.values or {}).get("messages", [])

    min_required = FRONTIER_KEEP + MIN_COMPACTABLE
    if len(messages) < min_required:
        return {
            "compacted": False,
            "reason": "not_enough_messages",
            "message_count": len(messages),
            "min_required": min_required,
        }

    # 2. Classify
    messages, protected_indices, compactable_indices, frontier_indices = classify_messages(messages)

    if len(compactable_indices) < MIN_COMPACTABLE:
        return {
            "compacted": False,
            "reason": "too_few_compactable",
            "message_count": len(messages),
            "compactable_count": len(compactable_indices),
            "protected_count": len(protected_indices),
            "frontier_count": len(frontier_indices),
            "min_compactable": MIN_COMPACTABLE,
        }

    original_count = len(messages)

    # 3. Generate summary
    try:
        summary_text = await generate_summary(messages, compactable_indices, model_id)
    except Exception as e:
        logger.error("Compaction summary generation failed: %s", e, exc_info=True)
        return {"compacted": False, "reason": "summary_generation_failed", "error": str(e)}

    # 4. Persist DB summary message
    try:
        # Get the seq of the last compacted message for audit trail
        last_compacted_seq = 0
        async with AsyncSessionLocal() as db:
            last_compacted_seq = await session_svc.get_last_message_seq(db, uuid.UUID(session_id))
            await session_svc.create_message(
                db,
                session_id=uuid.UUID(session_id),
                role="user",
                parts=[{"type": "text", "content": f"[Context Summary]\n{summary_text}"}],
                is_summary=True,
            )
            await db.commit()
    except Exception as e:
        logger.error("Compaction DB persist failed: %s", e, exc_info=True)
        return {"compacted": False, "reason": "db_persist_failed", "error": str(e)}

    # 5. Write context_summary.json
    try:
        write_context_summary_json(session_dir, summary_text, last_compacted_seq)
    except Exception as e:
        logger.warning("context_summary.json write failed: %s", e, exc_info=True)
        # Non-blocking — checkpoint rewrite still proceeds

    # 6. Rewrite checkpoint
    try:
        new_messages = await rewrite_checkpoint(
            agent, config, messages, summary_text,
            protected_indices, frontier_indices,
        )
    except Exception as e:
        logger.error("Checkpoint rewrite failed: %s", e, exc_info=True)
        return {"compacted": False, "reason": "checkpoint_rewrite_failed", "error": str(e)}

    # 7. Publish SSE
    if publish:
        try:
            await publish(session_id, {
                "event": "compaction_done",
                "original_count": original_count,
                "new_count": len(new_messages),
                "compacted_count": len(compactable_indices),
            })
        except Exception:
            pass

    result = {
        "compacted": True,
        "original_count": original_count,
        "new_count": len(new_messages),
        "compacted_count": len(compactable_indices),
        "protected_count": len(protected_indices),
        "frontier_count": len(frontier_indices),
    }
    logger.info("Compaction complete for session %s: %s", session_id, result)
    return result
