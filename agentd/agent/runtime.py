"""Agent Runtime — create_agent + middleware + checkpointer.

Contract reference: §8.1 (runtime architecture), §8.2 (middleware), §8.6 (checkpointer).

This module replaces the hand-written agent/graph.py and agent/nodes.py.
"""

from datetime import datetime
from pathlib import Path

import httpx
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from core.config import settings
from tools.base import ToolContext
from tools.registry import get_registry

# ── Layered Prompt Builder (§8.3) ─────────────────────────────────────────

PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_runtime_header(
    agent_id: str,
    session_dir: str,
    user_root: str,
    model_id: str,
    session_id: str,
) -> str:
    """Layer 1: Runtime Header — dynamic environment metadata."""
    return (
        f"## Environment\n"
        f"- Working directory: {session_dir}\n"
        f"- User home: {user_root}\n"
        f"- Skills directory: {user_root.rstrip('/')}/skills/\n"
        f"- Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"- Agent: {agent_id}\n"
        f"- Model: {model_id}\n"
        f"- Session: {session_id}\n"
    )


def _load_role_prompt(agent_id: str) -> str:
    """Layer 2: Role Prompt — agent persona and capabilities.

    Looks in prompts/roles/ first, falls back to prompts/ for compatibility.
    """
    roles_path = PROMPT_DIR / "roles" / f"{agent_id}.md"
    if roles_path.exists():
        return roles_path.read_text(encoding="utf-8")
    # Fallback to legacy flat layout
    legacy_path = PROMPT_DIR / f"{agent_id}.md"
    if legacy_path.exists():
        return legacy_path.read_text(encoding="utf-8")
    # Ultimate fallback to build role
    fallback = PROMPT_DIR / "roles" / "build.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return ""


def _load_rules_layer() -> str:
    """Layer 3: Rules — platform constraints loaded from rules/*.md."""
    rules_dir = PROMPT_DIR / "rules"
    if not rules_dir.exists():
        return ""
    # Fixed load order for deterministic prompts
    parts: list[str] = []
    for name in sorted(rules_dir.glob("*.md")):
        content = name.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
    if not parts:
        return ""
    return "## Platform Rules\n\n" + "\n\n".join(parts)


def _load_task_plan_layer(session_dir: str) -> str:
    """Layer 3.5: Task Plan — inject active task plan into prompt.

    When session_dir/.agentd/task_plan.json exists and active=true,
    injects the task title, summary, and step list. Only the in_progress
    step detail is included to reduce prompt noise (§brief 7).
    """
    import json

    plan_path = Path(session_dir) / ".agentd" / "task_plan.json"
    if not plan_path.exists():
        return ""

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    if not plan.get("active", False):
        return ""

    task = plan.get("task", {})
    steps = plan.get("steps", [])

    parts: list[str] = []
    parts.append("## Current Task Plan\n")
    if task.get("title"):
        parts.append(f"**Task:** {task['title']}")
    if task.get("summary"):
        parts.append(f"**Summary:** {task['summary']}")

    parts.append("\n### Steps\n")
    for s in steps:
        status_icon = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
            s.get("status", "pending"), "[ ]"
        )
        parts.append(f"- {status_icon} {s.get('title', '???')}")

    # Inject detail for in_progress steps only
    in_progress = [s for s in steps if s.get("status") == "in_progress"]
    if in_progress:
        parts.append("\n### Current Step Detail\n")
        for s in in_progress:
            if s.get("detail"):
                parts.append(f"**{s['title']}:** {s['detail']}")

    return "\n".join(parts)


def _load_skills_layer(loaded_skills: list[str] | None) -> str:
    """Layer 4: Skills — loaded skill content injected into system prompt."""
    if not loaded_skills:
        return ""
    # Deduplicate by content while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for skill in loaded_skills:
        if skill not in seen:
            seen.add(skill)
            unique.append(skill)
    return "## Loaded Skills\n\n" + "\n\n---\n\n".join(unique)


def build_system_prompt(
    agent_id: str,
    session_dir: str,
    user_root: str = "",
    model_id: str = "",
    session_id: str = "",
    loaded_skills: list[str] | None = None,
) -> str:
    """Build the system prompt via layered assembly (§8.3).

    Assembly order:
    1. Runtime Header (dynamic environment metadata)
    2. Role Prompt (agent persona from roles/{agent_id}.md)
    3. Rules Layer (platform constraints from rules/*.md)
    4. Skills Layer (loaded skill content, deduplicated)

    The final string is passed to create_agent(prompt=...).
    """
    layers: list[str] = []

    # Layer 1: Runtime Header
    layers.append(_load_runtime_header(agent_id, session_dir, user_root, model_id, session_id))

    # Layer 2: Role Prompt
    role = _load_role_prompt(agent_id)
    if role:
        layers.append(role)

    # Layer 3: Rules
    rules = _load_rules_layer()
    if rules:
        layers.append(rules)

    # Layer 3.5: Task Plan (active plan injected between rules and skills)
    task_plan = _load_task_plan_layer(session_dir)
    if task_plan:
        layers.append(task_plan)

    # Layer 4: Skills
    skills = _load_skills_layer(loaded_skills)
    if skills:
        layers.append(skills)

    prompt = "\n\n---\n\n".join(layers)

    if settings.debug:
        _debug_prompt_layers(agent_id, layers)

    return prompt


def _debug_prompt_layers(agent_id: str, layers: list[str]) -> None:
    """Print prompt layer summary when DEBUG=true."""
    total = sum(len(l) for l in layers)
    names = ["Runtime Header", "Role Prompt", "Rules", "Task Plan", "Skills"]
    print(f"[prompt] agent={agent_id} layers={len(layers)} chars={total}")
    for i, layer in enumerate(layers):
        label = names[i] if i < len(names) else f"Layer {i}"
        print(f"  [{label}] {len(layer)} chars")


# ── Skill content fetcher ──────────────────────────────────────────────────


async def _fetch_loaded_skill_content(
    session_id: str, user_root: str,
) -> list[str] | None:
    """Fetch loaded skill content for a session from the filesystem.

    Returns a list of skill content strings if the session has loaded skills,
    or None if no skills are loaded. This ensures skills survive compaction
    by reading from the session-level loaded_skills field (DB) and then
    fetching the actual content from the filesystem (user_root/skills/).
    """
    from core.database import AsyncSessionLocal
    from session import service as session_svc
    from workspace.manager import get_skills_dir
    import uuid

    try:
        async with AsyncSessionLocal() as db:
            session = await session_svc.get_session(db, uuid.UUID(session_id))
            if not session or not session.loaded_skills:
                return None
            skills_dir = get_skills_dir(user_root)
            contents: list[str] = []
            for entry in session.loaded_skills:
                # Support both new {"name":"..","version":".."} and legacy "name" format
                if isinstance(entry, dict):
                    name = entry.get("name", "")
                    version = entry.get("version", "0.1.0")
                else:
                    name = str(entry)
                    version = "0.1.0"
                if not name:
                    continue
                skill_md = Path(skills_dir) / name / "SKILL.md"
                if skill_md.exists():
                    content = skill_md.read_text(encoding="utf-8")
                    contents.append(f"[Skill: {name} v{version}]\n\n{content}")
            return contents if contents else None
    except Exception:
        return None


# ── HITL middleware configuration (§8.2) ───────────────────────────────────

_HITL_INTERRUPT_ON: dict[str, bool] = {
    "bash": True,
    "file_write": True,
    "file_edit": True,
    # file_read, skill, list_dir, glob, grep are NOT listed → auto-approved
}


def _build_hitl_middleware(session_dir: str = "") -> HumanInTheLoopMiddleware:
    """Build HITL middleware, respecting FSD mode.

    In FSD mode, interrupt_on is empty — no tools trigger HITL interrupts.
    Safety (path validation, bash blacklist, timeouts) stays in the tool layer.
    """
    if session_dir:
        from permission.policy import load_policy
        policy = load_policy(session_dir)
        if policy.mode == "fsd":
            return HumanInTheLoopMiddleware(interrupt_on={})
    return HumanInTheLoopMiddleware(interrupt_on=_HITL_INTERRUPT_ON)


def _build_summarization_middleware(llm) -> SummarizationMiddleware:
    """Build context summarization middleware (§8.2).

    Triggers when context exceeds 75% of configured context window,
    keeps the 20 most recent messages after summarization.
    """
    return SummarizationMiddleware(
        model=llm,
        trigger=("tokens", int(settings.context_window_tokens * 0.75)),
        keep=("messages", 20),
    )


# ── Checkpointer (§8.6) ───────────────────────────────────────────────────

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


async def get_checkpointer() -> AsyncPostgresSaver:
    """Lazy-init the PostgreSQL checkpointer (psycopg3 pool)."""
    global _pool, _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    _pool = AsyncConnectionPool(
        conninfo=settings.checkpoint_db_url,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
        },
    )
    await _pool.open()

    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()
    return _checkpointer


# ── Agent factory ──────────────────────────────────────────────────────────


async def build_agent(
    session_id: str,
    user_id: str,
    user_root: str,
    session_dir: str,
    agent_id: str,
    model_id: str,
):
    """Create a compiled agent for a specific session.

    Each session gets its own agent instance because tools need a
    session-specific ToolContext (user_root, session_dir, etc.).

    Returns a compiled StateGraph that supports astream / aget_state.
    """
    # 1. Model — resolve base_url/api_key from DB config or env fallback (Phase I2)
    # trust_env=False: prevent httpx from inheriting HTTP_PROXY / HTTPS_PROXY (#29).
    from core.database import AsyncSessionLocal
    from model_config.service import resolve_active_model_config

    async with AsyncSessionLocal() as db:
        resolved = await resolve_active_model_config(db)

    llm = ChatOpenAI(
        model=model_id,
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        streaming=True,
        http_async_client=httpx.AsyncClient(trust_env=False),
    )

    # 2. Tools (bound to session context)
    registry = get_registry()
    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        user_root=user_root,
        session_dir=session_dir,
        venv_bin=user_root.rstrip("/") + "/.venv/bin/",
        publish=None,  # SSE events are handled by runner, not tools
    )
    tools: list[StructuredTool] = registry.get_langchain_tools(ctx)

    # 3. System prompt (layered assembly)
    # Fetch loaded skills from session DB + filesystem for prompt injection (survives compaction)
    loaded_skills = await _fetch_loaded_skill_content(session_id, user_root)

    system_prompt = build_system_prompt(
        agent_id=agent_id,
        session_dir=session_dir,
        user_root=user_root,
        model_id=model_id,
        session_id=session_id,
        loaded_skills=loaded_skills,
    )

    # 4. Middleware (FSD mode disables HITL interrupts)
    hitl = _build_hitl_middleware(session_dir)
    summarization = _build_summarization_middleware(llm)

    # 5. Checkpointer
    checkpointer = await get_checkpointer()

    # 6. Create agent
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[hitl, summarization],
        checkpointer=checkpointer,
    )

    return agent
