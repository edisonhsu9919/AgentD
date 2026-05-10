"""Rolling Session Memory (Phase P4-C).

Maintains a single `.agentd/session_memory.md` file that is continuously
updated in the background after each agent run. This memory:

- Is NOT injected into the prompt during pre_hard_compact
- Serves as the primary input for hard compact (P4-D)
- Uses fixed chapter headings that are never changed, only their content
- Is updated via VLM (compaction model) doing incremental patch, not full rewrite

File structure:
  .agentd/session_memory.md       — structured Markdown memory snapshot
  .agentd/session_memory_meta.json — metadata (boundary, version, state)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage as LCHumanMessage

logger = logging.getLogger(__name__)

# ── Fixed chapter template ───���──────────────────────────────────────────────

MEMORY_TEMPLATE = """\
# Session Title
(untitled session)

# Current State
(no state recorded yet)

# Task Specification
(no task specified)

# Files and Artifacts
(none)

# Workflow Patterns
(none observed)

# Errors & Corrections
(none)

# Active Skill / Plan
(none)

# Subtasks
(none)

# Key Results
(none)

# Next Steps
(none)

# Worklog
(empty)
"""

MEMORY_CHAPTERS = [
    "Session Title",
    "Current State",
    "Task Specification",
    "Files and Artifacts",
    "Workflow Patterns",
    "Errors & Corrections",
    "Active Skill / Plan",
    "Subtasks",
    "Key Results",
    "Next Steps",
    "Worklog",
]

# ── Thresholds ──────────────────────────────────────────────────────────────

FIRST_BUILD_TOKEN_THRESHOLD = 10_000      # first memory build after this many tokens
INCREMENTAL_TOKEN_THRESHOLD = 5_000       # subsequent patches after this many new tokens
MIN_TOOL_CALLS_TRIGGER = 3               # alternative trigger: N tool calls since last update
RECOMPRESSION_TOKEN_LIMIT = 12_000        # trigger recompression when memory exceeds this
RECOMPRESSION_CHAPTER_LIMIT = 2_000       # trigger when any single chapter exceeds this

# Rough estimate: 1 token ≈ 4 chars for English, ~2 chars for Chinese
CHARS_PER_TOKEN = 3


# ── File operations ───────���─────────────────────────────────────────────────

def get_memory_path(session_dir: str) -> str:
    return os.path.join(session_dir, ".agentd", "session_memory.md")


def get_meta_path(session_dir: str) -> str:
    return os.path.join(session_dir, ".agentd", "session_memory_meta.json")


def read_memory(session_dir: str) -> str | None:
    """Read current session_memory.md, or None if not yet created."""
    path = get_memory_path(session_dir)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_memory(session_dir: str, content: str) -> None:
    """Write session_memory.md."""
    path = get_memory_path(session_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_meta(session_dir: str) -> dict[str, Any]:
    """Read session_memory_meta.json, or return defaults."""
    path = get_meta_path(session_dir)
    if not os.path.isfile(path):
        return _default_meta()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_meta(session_dir: str, meta: dict[str, Any]) -> None:
    """Write session_memory_meta.json."""
    path = get_meta_path(session_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _default_meta() -> dict[str, Any]:
    return {
        "memory_version": 1,
        "memory_valid": False,
        "snapshot_version": 0,
        "compacted_through_seq": 0,
        "boundary_seq": 0,
        "last_memory_refresh_at": None,
        "memory_token_estimate": 0,
        "pre_hard_compact": True,
        "post_hard_compact": False,
        "last_hard_compaction_at": None,
    }


# ── Token estimation ────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count."""
    return len(text) // CHARS_PER_TOKEN


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens across a list of LangChain messages."""
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") and isinstance(msg.content, str) else ""
        total += estimate_tokens(content)
    return total


# ── Should we update memory? ───────────────────���────────────────────────────

def should_update_memory(
    session_dir: str,
    messages: list,
    last_message_seq: int,
) -> bool:
    """Determine if memory should be updated based on thresholds.

    Returns True if enough new content has accumulated since last update.
    """
    meta = read_meta(session_dir)

    compacted_through = meta.get("compacted_through_seq", 0)

    # Count new messages since last memory update
    new_messages = [m for i, m in enumerate(messages) if i > compacted_through]
    if not new_messages:
        return False

    new_tokens = estimate_messages_tokens(new_messages)

    # Count tool calls in new messages
    from langchain_core.messages import ToolMessage
    new_tool_calls = sum(1 for m in new_messages if isinstance(m, ToolMessage))

    # First build
    if not meta.get("memory_valid"):
        return new_tokens >= FIRST_BUILD_TOKEN_THRESHOLD

    # Incremental update
    if new_tokens >= INCREMENTAL_TOKEN_THRESHOLD:
        return True
    if new_tool_calls >= MIN_TOOL_CALLS_TRIGGER:
        return True

    return False


# ── Memory patch prompt ───────��─────────────────────────────────────────────

_PATCH_PROMPT = """\
You are a session memory manager. Your job is to update a structured Markdown \
memory document based on new conversation content.

## Rules
1. ONLY update chapter content — NEVER change chapter headings (lines starting with #).
2. Each chapter heading must remain EXACTLY as-is.
3. Replace "(none)", "(empty)", "(untitled session)", "(no state recorded yet)" etc. with actual content when relevant information appears.
4. For chapters with existing content, MERGE new information — don't discard previous content unless it's clearly outdated.
5. Keep each chapter concise (under 500 words). Summarize rather than copy verbatim.
6. The Worklog chapter should append new entries, not replace old ones.
7. Output the COMPLETE updated document, including all chapters.

## Current Memory
```markdown
{current_memory}
```

## New Conversation Content (since last update)
```
{new_content}
```

## Instructions
Output the updated session_memory.md with all chapters. Only output the Markdown content, no fences or explanations."""


def _build_patch_prompt(current_memory: str, new_messages: list) -> str:
    """Build the prompt for VLM to patch the memory."""
    # Extract text content from new messages
    parts = []
    for msg in new_messages:
        role = getattr(msg, "type", "unknown")
        content = msg.content if hasattr(msg, "content") and isinstance(msg.content, str) else ""
        if content:
            # Truncate very long individual messages
            if len(content) > 3000:
                content = content[:3000] + "... [truncated]"
            parts.append(f"[{role}] {content}")

    new_content = "\n\n".join(parts)
    # Cap total new content to avoid overwhelming the model
    if len(new_content) > 15000:
        new_content = new_content[:15000] + "\n... [remaining content truncated]"

    return _PATCH_PROMPT.format(
        current_memory=current_memory,
        new_content=new_content,
    )


# ── VLM text call (reuses VLM provider config) ─────────────────────────────

async def _call_compaction_model(prompt: str, max_tokens: int = 4096) -> str | None:
    """Call the maintenance sidecar model for text-only memory tasks."""
    try:
        from core.database import AsyncSessionLocal
        from agent.maintenance_model import invoke_maintenance_chat

        async with AsyncSessionLocal() as db:
            result, resolved = await invoke_maintenance_chat(
                db,
                purpose="session_memory",
                messages=[LCHumanMessage(content=prompt)],
                max_tokens=max_tokens,
            )

        if result is None:
            logger.warning(
                "No maintenance model configured for session memory: %s",
                getattr(resolved, "disabled_reason", "disabled"),
            )
            return None

        content = result.content if isinstance(result.content, str) else ""
        if not content.strip():
            logger.warning(
                "Maintenance model returned empty content for session memory "
                "(model=%s)",
                getattr(resolved, "model_id", ""),
            )
            return None

        return content

    except Exception as e:
        logger.warning("Session memory maintenance model call failed: %s", e)
        return None


# ── Core update logic ──────────────��────────────────────────────────��───────

async def update_session_memory(
    session_dir: str,
    messages: list,
    session_id: str = "",
) -> bool:
    """Update session_memory.md based on new messages.

    Called asynchronously after _finalize. Returns True if memory was updated.
    """
    meta = read_meta(session_dir)
    current_memory = read_memory(session_dir)

    # First build: use template
    if current_memory is None:
        current_memory = MEMORY_TEMPLATE

    # Get new messages since last update
    compacted_through = meta.get("compacted_through_seq", 0)
    new_messages = [m for i, m in enumerate(messages) if i > compacted_through]

    if not new_messages:
        return False

    # Build patch prompt
    prompt = _build_patch_prompt(current_memory, new_messages)

    # Call compaction model
    updated_memory = await _call_compaction_model(prompt)
    if not updated_memory:
        logger.warning("Memory patch failed for session %s — keeping old memory", session_id[:8])
        return False

    # Validate: must contain at least some chapter headings
    if not _validate_memory_structure(updated_memory):
        logger.warning("Memory patch produced invalid structure — discarding")
        return False

    # Write updated memory
    write_memory(session_dir, updated_memory)

    # Check if recompression is needed
    mem_tokens = estimate_tokens(updated_memory)
    needs_recompression = mem_tokens > RECOMPRESSION_TOKEN_LIMIT
    if not needs_recompression:
        # Check individual chapters
        for chapter, content in _parse_chapters(updated_memory).items():
            if estimate_tokens(content) > RECOMPRESSION_CHAPTER_LIMIT:
                needs_recompression = True
                break

    if needs_recompression:
        recompressed = await _recompress_memory(updated_memory)
        if recompressed:
            write_memory(session_dir, recompressed)
            mem_tokens = estimate_tokens(recompressed)

    # Update meta
    meta["memory_valid"] = True
    meta["snapshot_version"] = meta.get("snapshot_version", 0) + 1
    meta["compacted_through_seq"] = len(messages) - 1
    meta["last_memory_refresh_at"] = datetime.now(timezone.utc).isoformat()
    meta["memory_token_estimate"] = mem_tokens
    write_meta(session_dir, meta)

    logger.info(
        "Session memory updated: session=%s version=%d tokens=%d",
        session_id[:8], meta["snapshot_version"], mem_tokens,
    )
    return True


def _validate_memory_structure(content: str) -> bool:
    """Check that the memory contains at least the core chapter headings."""
    required = ["# Current State", "# Task Specification", "# Next Steps"]
    return all(heading in content for heading in required)


def _parse_chapters(content: str) -> dict[str, str]:
    """Parse memory into {chapter_name: content} dict."""
    chapters: dict[str, str] = {}
    current_chapter = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("# "):
            if current_chapter:
                chapters[current_chapter] = "\n".join(current_lines).strip()
            current_chapter = line[2:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_chapter:
        chapters[current_chapter] = "\n".join(current_lines).strip()

    return chapters


# ── Memory-on-memory recompression ──────────────────────────────────────────

_RECOMPRESS_PROMPT = """\
You are a session memory compressor. The following memory document has grown too large. \
Compress it while preserving all critical information.

## Rules
1. Keep ALL chapter headings exactly as-is.
2. Prioritize keeping: Current State, Task Specification, Active Skill / Plan, Next Steps.
3. Aggressively compress: Workflow Patterns, Worklog, old Errors & Corrections.
4. Remove redundant or outdated entries.
5. Each chapter should be under 400 words after compression.
6. Output the COMPLETE compressed document with all chapters.

## Memory to Compress
```markdown
{memory}
```

Output only the compressed Markdown, no fences or explanations."""


async def _recompress_memory(memory: str) -> str | None:
    """Recompress an oversized memory document."""
    prompt = _RECOMPRESS_PROMPT.format(memory=memory)
    result = await _call_compaction_model(prompt, max_tokens=4096)

    if result and _validate_memory_structure(result):
        logger.info("Memory recompressed: %d → %d chars", len(memory), len(result))
        return result

    return None
