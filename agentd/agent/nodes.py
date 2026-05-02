"""DEPRECATED — replaced by agent/runtime.py (create_agent + middleware).

This file is no longer used. All functionality has been migrated to:
- agent/runtime.py (agent construction, prompt assembly, HITL middleware)
- agent/runner.py (streaming, SSE events, message persistence)
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from sqlalchemy.exc import IntegrityError

from agent.state import AgentState
from core.config import settings
from core.events import event_bus
from tools.registry import get_registry

# Fixed namespace for deterministic permission UUIDs (replay-safe)
_PERM_NS = uuid.UUID("b7e23ec2-9c7e-4a73-b5f0-6e5ae3a9e654")

# ── Prompt loading (§8.4) ────────────────────────────────────────────────────

PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_base_prompt(agent_id: str) -> str:
    path = PROMPT_DIR / f"{agent_id}.md"
    if not path.exists():
        path = PROMPT_DIR / "build.md"  # fallback
    return path.read_text(encoding="utf-8")


# ── LLM client factory ──────────────────────────────────────────────────────

def _get_llm(model_id: str) -> ChatOpenAI:
    """Create a ChatOpenAI instance pointing to the configured endpoint."""
    registry = get_registry()
    tools_schemas = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.schema(),
            },
        }
        for tool in registry.tools.values()
    ]

    llm = ChatOpenAI(
        model=model_id,
        base_url=settings.local_llm_url,
        api_key=settings.llm_api_key,
        streaming=True,
    )
    return llm.bind_tools(
        [tool_s["function"] for tool_s in tools_schemas],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Node: call_llm  (§8.1 / §8.4)
# ══════════════════════════════════════════════════════════════════════════════

async def call_llm(state: AgentState) -> dict[str, Any]:
    """Invoke the LLM with streaming, publish text_delta events."""
    session_id = state["session_id"]

    # 1. Build system prompt (§8.4 — locked order)
    system_text = _load_base_prompt(state["agent_id"]).format(
        workspace=state["workspace"],
        date=datetime.now().strftime("%Y-%m-%d"),
        agent_id=state["agent_id"],
    )

    # 2. Append loaded skills
    if state.get("loaded_skills"):
        skills_block = "\n\n---\n## Loaded Skills\n\n" + \
                       "\n\n---\n\n".join(state["loaded_skills"])
        system_text += skills_block

    # 3. Assemble message list
    messages = [SystemMessage(content=system_text)] + state["messages"]

    # 4. Call LLM with streaming
    llm = _get_llm(state["model_id"])
    message_id = str(uuid.uuid4())

    collected_text = ""
    ai_message: AIMessage | None = None

    async for chunk in llm.astream(messages):
        if isinstance(chunk, AIMessage):
            if ai_message is None:
                ai_message = chunk
            else:
                ai_message = ai_message + chunk

            # Publish text deltas
            if chunk.content:
                collected_text += chunk.content
                await event_bus.publish(session_id, {
                    "event": "text_delta",
                    "message_id": message_id,
                    "content": chunk.content,
                })

    if ai_message is None:
        ai_message = AIMessage(content="")

    # Update token usage (estimate from content length if not available)
    token_usage = state["token_usage"].copy()
    usage_meta = getattr(ai_message, "usage_metadata", None)
    if usage_meta:
        token_usage["input"] += usage_meta.get("input_tokens", 0)
        token_usage["output"] += usage_meta.get("output_tokens", 0)
        token_usage["total"] = token_usage["input"] + token_usage["output"]

    step_count = state["step_count"] + 1

    return {
        "messages": [ai_message],
        "token_usage": token_usage,
        "step_count": step_count,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Node: execute_tools  (§8.1 / §8.3 / §8.5)
# ══════════════════════════════════════════════════════════════════════════════

async def execute_tools(state: AgentState) -> dict[str, Any]:
    """Execute pending tool calls from the last AI message.

    Permission flow uses LangGraph ``interrupt()`` per §8.3.
    SkillTool load results are written back to loaded_skills per §8.5.
    """
    from tools.base import ToolContext

    session_id = state["session_id"]
    last_msg = state["messages"][-1]

    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return {}

    registry = get_registry()
    tool_messages: list[ToolMessage] = []
    loaded_skills = list(state.get("loaded_skills", []))

    for tc in last_msg.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_call_id = tc["id"]

        tool = registry.get(tool_name)
        if tool is None:
            tool_messages.append(ToolMessage(
                content=f"Unknown tool: {tool_name}",
                tool_call_id=tool_call_id,
            ))
            continue

        # ── Permission check (§8.3) ──────────────────────────────────────
        permission = registry.default_permission(tool_name)
        if permission == "ask":
            # Create permission_request record in DB
            permission_id = await _create_permission_record(
                session_id, tool_call_id, tool_name, tool_args,
            )

            # Publish permission_ask event
            await event_bus.publish(session_id, {
                "event": "permission_ask",
                "permission_id": permission_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": tool_args,
            })

            # Interrupt the graph — caller resumes with Command(resume=...)
            result = interrupt({
                "permission_id": permission_id,
                "tool_name": tool_name,
                "input": tool_args,
            })

            if isinstance(result, dict) and result.get("decision") == "denied":
                tool_messages.append(ToolMessage(
                    content="Permission denied by user",
                    tool_call_id=tool_call_id,
                ))
                await event_bus.publish(session_id, {
                    "event": "permission_resolved",
                    "permission_id": permission_id,
                    "decision": "denied",
                })
                continue

            await event_bus.publish(session_id, {
                "event": "permission_resolved",
                "permission_id": permission_id,
                "decision": "approved",
            })

        # ── Execute tool ─────────────────────────────────────────────────
        ctx = ToolContext(
            user_id=state["user_id"],
            session_id=session_id,
            workspace=state["workspace"],
            venv_bin=state["workspace"].rstrip("/") + "/.venv/bin/",
            publish=event_bus.publish,
        )

        # Publish tool_start
        await event_bus.publish(session_id, {
            "event": "tool_start",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "input": tool_args,
        })

        try:
            result = await tool.execute(ctx, **tool_args)
        except Exception as e:
            result = {"output": str(e), "is_error": True}

        output = result.get("output", "")
        is_error = result.get("is_error", False)

        # Publish tool_result
        await event_bus.publish(session_id, {
            "event": "tool_result",
            "tool_call_id": tool_call_id,
            "output": str(output) if not isinstance(output, str) else output,
            "is_error": is_error,
        })

        tool_messages.append(ToolMessage(
            content=str(output) if not isinstance(output, str) else output,
            tool_call_id=tool_call_id,
        ))

        # ── SkillTool writeback (§8.5) ───────────────────────────────────
        if tool_name == "skill" and result.get("action") == "load":
            skill_content = result.get("content", "")
            if skill_content and skill_content not in loaded_skills:
                loaded_skills.append(skill_content)

    updates: dict[str, Any] = {"messages": tool_messages}
    if loaded_skills != state.get("loaded_skills", []):
        updates["loaded_skills"] = loaded_skills

    return updates


async def _create_permission_record(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    tool_input: dict,
) -> str:
    """Create a permission_request record in the DB. Returns the permission_id as string."""
    from core.database import AsyncSessionLocal
    from permission import service as perm_svc

    permission_id = uuid.uuid4()
    try:
        async with AsyncSessionLocal() as db:
            await perm_svc.create_permission_request(
                db,
                session_id=uuid.UUID(session_id),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_input=tool_input,
                permission_id=permission_id,
            )
            await db.commit()
    except Exception:
        pass  # best-effort; the interrupt will still work without DB record
    return str(permission_id)


# ══════════════════════════════════════════════════════════════════════════════
# Node: compact_context  (§8.1)
# ══════════════════════════════════════════════════════════════════════════════

async def compact_context(state: AgentState) -> dict[str, Any]:
    """Summarize conversation history when approaching the context window limit.

    Uses the same LLM to produce a summary, then replaces old messages with
    a single summary message.
    """
    session_id = state["session_id"]
    messages = state["messages"]

    try:
        from agent.checkpoint_state import analyze_tool_adjacency

        if analyze_tool_adjacency(messages).orphan_tool_call_ids:
            return {}
    except Exception:
        return {}

    # Keep at least the last 4 messages intact
    if len(messages) <= 6:
        return {}

    to_summarize = messages[:-4]
    to_keep = messages[-4:]

    # Build a summarization prompt
    summary_messages = [
        SystemMessage(content=(
            "You are a conversation summarizer. Produce a concise summary of the "
            "following conversation. Focus on key decisions, code changes, and "
            "pending tasks. Output ONLY the summary text."
        )),
        HumanMessage(content="\n\n".join(
            f"[{getattr(m, 'type', 'unknown')}]: {m.content[:500]}"
            for m in to_summarize if hasattr(m, "content") and m.content
        )),
    ]

    llm = ChatOpenAI(
        model=state["model_id"],
        base_url=settings.local_llm_url,
        api_key=settings.llm_api_key,
        streaming=False,
    )

    try:
        resp = await llm.ainvoke(summary_messages)
        summary_text = resp.content
    except Exception:
        # If summarization fails, just keep going without compaction
        return {}

    # Estimate tokens saved (rough: 4 chars ≈ 1 token)
    original_chars = sum(len(m.content) for m in to_summarize if hasattr(m, "content") and m.content)
    tokens_saved = max(0, (original_chars - len(summary_text)) // 4)

    # Replace old messages with the summary
    summary_msg = HumanMessage(content=f"[Context Summary]\n{summary_text}")
    new_token_usage = state["token_usage"].copy()

    await event_bus.publish(session_id, {
        "event": "compaction",
        "tokens_saved": tokens_saved,
    })

    return {
        "messages": [summary_msg] + to_keep,
        "token_usage": new_token_usage,
    }
