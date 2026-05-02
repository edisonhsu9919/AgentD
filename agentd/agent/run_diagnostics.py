"""Run diagnostics assembly for executor split."""

from __future__ import annotations

import re
import traceback
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.diagnostics import build_checkpoint_diagnostics
from agent.message_persistence import persist_messages
from core.config import settings
from core.database import AsyncSessionLocal


async def record_run_diagnostics(
    agent,
    session_id: str,
    messages: list,
    *,
    snapshot=None,
) -> None:
    try:
        from agent import scheduler

        prompt_diag = getattr(agent, "_prompt_diagnostics", {})

        ai_count = sum(1 for m in messages if isinstance(m, AIMessage))
        tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
        human_count = sum(1 for m in messages if isinstance(m, HumanMessage))
        system_count = sum(1 for m in messages if isinstance(m, SystemMessage))

        token_usage = extract_token_usage(messages)
        last_call = extract_last_call_usage(messages)

        checkpoint_composition = {
            "human": human_count,
            "ai": ai_count,
            "tool": tool_count,
            "system": system_count,
            "total": len(messages),
        }
        checkpoint_diag = build_checkpoint_diagnostics(
            messages=messages,
            snapshot=snapshot,
        )

        skill_re = re.compile(r"^\[Skill: (.+?) v(.+?)\]")
        active_skill_names: list[str] = []
        skill_seen: set[str] = set()
        last_skill_load_idx: int = -1
        first_plan_idx: int = -1
        for idx, msg in enumerate(messages):
            if isinstance(msg, ToolMessage) and msg.content:
                sm = skill_re.match(msg.content)
                if sm:
                    sname = sm.group(1)
                    if sname not in skill_seen:
                        skill_seen.add(sname)
                        active_skill_names.append(sname)
                    last_skill_load_idx = idx
                elif getattr(msg, "name", "") == "planning" and first_plan_idx < 0:
                    first_plan_idx = idx

        from tools.registry import get_tool_loop_guard_diagnostics

        diagnostics = {
            **prompt_diag,
            "history_message_count": len(messages),
            "history_ai_count": ai_count,
            "history_tool_count": tool_count,
            "history_human_count": human_count,
            "prompt_tokens": token_usage["input"],
            "completion_tokens": token_usage["output"],
            "total_tokens": token_usage["total"],
            "last_call_prompt_tokens": last_call["prompt_tokens"],
            "last_call_completion_tokens": last_call["completion_tokens"],
            "last_call_total_tokens": last_call["total_tokens"],
            "last_call_cache_read_tokens": last_call["cache_read_tokens"],
            "last_call_cache_creation_tokens": last_call["cache_creation_tokens"],
            "checkpoint_composition": checkpoint_composition,
            **checkpoint_diag,
            "skill_loads_this_run": len(active_skill_names),
            "active_skill_names": active_skill_names,
            "plan_after_skill_load": (
                first_plan_idx > last_skill_load_idx
                if last_skill_load_idx >= 0 and first_plan_idx >= 0
                else None
            ),
            **get_microcompact_diagnostics(agent),
            **get_runtime_integrity_gate_diagnostics(agent),
            **get_runtime_integrity_warning_diagnostics(agent),
            **get_compaction_mode_diagnostics(getattr(agent, "_session_dir", None)),
            **get_tool_loop_guard_diagnostics(session_id),
            **get_exception_diagnostics(agent),
            **get_transcript_integrity_diagnostics(agent),
        }

        async with AsyncSessionLocal() as db:
            run = await scheduler.get_active_run(db, uuid.UUID(session_id))
            if run:
                diagnostics["run_type"] = run.run_type
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


async def record_tool_loop_failure(agent, config: dict, session_id: str) -> None:
    snapshot = await agent.aget_state(config)
    messages = snapshot.values.get("messages", []) if snapshot else []
    if messages:
        await persist_messages(session_id, messages)
    await record_run_diagnostics(agent, session_id, messages, snapshot=snapshot)


def get_compaction_mode_diagnostics(session_dir: str | None) -> dict:
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


def get_microcompact_diagnostics(agent) -> dict:
    mc = getattr(agent, "_microcompact_result", None)
    if not mc:
        return {"microcompact_applied": False}
    return {
        "microcompact_applied": mc.get("applied", False),
        "microcompact_removed_count": mc.get("removed_count", 0),
        "microcompact_replaced_count": mc.get("replaced_count", 0),
        "microcompact_reason": mc.get("reason", ""),
    }


def get_runtime_integrity_gate_diagnostics(agent) -> dict:
    gate = getattr(agent, "_runtime_integrity_gate", None)
    if not isinstance(gate, dict):
        return {}
    return {"runtime_integrity_gate": gate}


def get_runtime_integrity_warning_diagnostics(agent) -> dict:
    warning = getattr(agent, "_runtime_integrity_warning", None)
    if not isinstance(warning, dict):
        return {}
    return {"runtime_integrity_warning": warning}


def get_transcript_integrity_diagnostics(agent) -> dict:
    error = getattr(agent, "_transcript_integrity_error", None)
    if not error:
        return {}
    code = error.get("code", "TRANSCRIPT_INTEGRITY_ERROR")
    issues = error.get("issues", [])
    diagnostics = {
        "transcript_integrity_error": code,
        "transcript_integrity_issues": issues,
    }
    if code == "PROVIDER_PAYLOAD_VALIDATION_ERROR":
        diagnostics.update({
            "provider_payload_validation_error": True,
            "provider_payload_issues": issues,
        })
    return diagnostics


def get_exception_diagnostics(agent) -> dict:
    diagnostics = getattr(agent, "_run_exception_diagnostics", None)
    if not isinstance(diagnostics, dict):
        return {}
    return diagnostics


def extract_token_usage(messages: list) -> dict:
    total_input = 0
    total_output = 0
    for msg in messages:
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if usage and isinstance(usage, dict):
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
    return {"input": total_input, "output": total_output, "total": total_input + total_output}


def extract_last_call_usage(messages: list) -> dict:
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
