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


def _load_skills_metadata_layer(loaded_skills: list[dict] | None) -> str:
    """Layer 4: Session Skill Metadata — installed skill catalog (Phase M1).

    Injected from the user's installed skill catalog on disk.
    Only name/version/description/tags are in the system prompt.
    Full SKILL.md content enters the conversation via `skill load` ToolMessages.
    Uses OpenCode-style structured format for model readability.
    """
    if not loaded_skills:
        return ""
    # Deduplicate by name while preserving order
    seen: set[str] = set()
    unique: list[dict] = []
    for skill in loaded_skills:
        name = skill.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(skill)
    if not unique:
        return ""
    lines = ["## Available Session Skills", "", "<available_session_skills>"]
    for s in unique:
        name = s.get("name", "unknown")
        version = s.get("version", "0.1.0")
        desc = s.get("description", "")
        tags = s.get("tags", [])
        tag_attr = f' tags="{",".join(tags)}"' if tags else ""
        lines.append(f'  <skill name="{name}" version="{version}"{tag_attr}>{desc}</skill>')
    lines.append("</available_session_skills>")
    lines.append("")
    lines.append(
        "These skills are available to this session. "
        "When a task matches a skill's description, use `skill load <name>` directly — "
        "do NOT call `skill list` to rediscover the same catalog. "
        "Use `skill list` only for explicit discovery or troubleshooting."
    )
    return "\n".join(lines)


def _has_compaction_occurred(session_dir: str) -> bool:
    """Check whether this session has undergone at least one compaction.

    Reads context_summary.json and checks compaction_count > 0.
    Returns False on any error (file missing, corrupt, etc.).
    """
    import json as _json

    summary_path = Path(session_dir) / ".agentd" / "context_summary.json"
    if not summary_path.exists():
        return False
    try:
        data = _json.loads(summary_path.read_text(encoding="utf-8"))
        return data.get("compaction_count", 0) > 0
    except (ValueError, OSError):
        return False


def _load_compaction_context_layer(session_dir: str) -> str:
    """Layer 5: Compaction Context — inject structured summary after compaction.

    When session_dir/.agentd/context_summary.json exists and is structured,
    injects key fields so the model can recover session context after compaction.
    This layer is only active after at least one compaction has occurred.

    Phase N2: enriched with key_decisions, conversation_highlights, and
    active skill metadata cross-reference for stronger post-compaction continuity.
    """
    import json as _json

    summary_path = Path(session_dir) / ".agentd" / "context_summary.json"
    if not summary_path.exists():
        return ""

    try:
        data = _json.loads(summary_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""

    # Only inject if we have a structured summary
    if not data.get("structured", True):
        return ""

    parts: list[str] = ["## Prior Context (from compaction)"]
    parts.append("")

    intent = data.get("session_intent", "")
    if intent:
        parts.append(f"**Session Intent:** {intent}")

    task_state = data.get("current_task_state", "")
    if task_state:
        parts.append(f"**Current Task State:** {task_state}")

    # Phase N2: key decisions with reasoning context
    decisions = data.get("key_decisions", [])
    if decisions:
        parts.append("**Key Decisions:**")
        for d in decisions[:8]:
            parts.append(f"- {d}")

    active_skill = data.get("active_skill")
    if active_skill:
        parts.append(f"**Active Skill:** {active_skill}")

    artifacts = data.get("important_artifacts", [])
    if artifacts:
        parts.append("**Important Artifacts:**")
        for a in artifacts[:10]:  # Cap to avoid prompt bloat
            parts.append(f"- {a}")

    # Phase N2: conversation highlights for richer context recovery
    highlights = data.get("conversation_highlights", [])
    if highlights:
        parts.append("**Conversation Highlights:**")
        for h in highlights[:8]:
            parts.append(f"- {h}")

    next_steps = data.get("next_steps", [])
    if next_steps:
        parts.append("**Next Steps:**")
        for s in next_steps[:5]:
            parts.append(f"- {s}")

    count = data.get("compaction_count", 0)
    if count:
        parts.append(f"\n_This session has been compacted {count} time(s)._")

    return "\n".join(parts)


def build_system_prompt(
    agent_id: str,
    session_dir: str,
    user_root: str = "",
    model_id: str = "",
    session_id: str = "",
    loaded_skills: list[dict] | None = None,
) -> tuple[str, dict]:
    """Build the system prompt via layered assembly (§8.3).

    Assembly order (Phase L prompt strategy + Phase N1 compaction context):
    1. Runtime Header (dynamic environment metadata)
    2. Role Prompt (agent persona from roles/{agent_id}.md)
    3. Rules Layer (platform constraints from rules/*.md)
    4. Skills Metadata Layer (frontmatter only — name/version/description)
    5. Compaction Context (structured summary from context_summary.json)

    Task Plan is NO LONGER injected here (Phase L prompt strategy).
    Plan state lives in the conversation flow via planning/todo_update ToolMessages.

    Returns (prompt_string, diagnostics_dict).
    """
    layers: list[str] = []
    layer_sizes: dict[str, int] = {}

    # Layer 1: Runtime Header
    header = _load_runtime_header(agent_id, session_dir, user_root, model_id, session_id)
    layers.append(header)
    layer_sizes["header"] = len(header)

    # Layer 2: Role Prompt
    role = _load_role_prompt(agent_id)
    if role:
        layers.append(role)
    layer_sizes["role"] = len(role) if role else 0

    # Layer 3: Rules
    rules = _load_rules_layer()
    if rules:
        layers.append(rules)
    layer_sizes["rules"] = len(rules) if rules else 0

    # Task Plan: conditionally injected as compact-after fallback (Phase N2-1).
    # Normally, plan state lives in checkpoint messages via planning/todo_update tools.
    # After compaction, those ToolMessages may be summarized away, so we fall back to
    # the persisted task_plan.json file to ensure plan continuity.
    task_plan = ""
    if _has_compaction_occurred(session_dir):
        task_plan = _load_task_plan_layer(session_dir)
        if task_plan:
            layers.append(task_plan)
    layer_sizes["task_plan"] = len(task_plan) if task_plan else 0

    # Layer 4: Skills Metadata (frontmatter only — OpenCode style)
    skills = _load_skills_metadata_layer(loaded_skills)
    if skills:
        layers.append(skills)
    layer_sizes["skills"] = len(skills) if skills else 0

    # Layer 5: Compaction Context (Phase N1 — post-compaction structured context)
    compaction_ctx = _load_compaction_context_layer(session_dir)
    if compaction_ctx:
        layers.append(compaction_ctx)
    layer_sizes["compaction_context"] = len(compaction_ctx) if compaction_ctx else 0

    prompt = "\n\n---\n\n".join(layers)

    if settings.debug:
        _debug_prompt_layers(agent_id, layers)

    # Phase L §12.4/12.6: prompt assembly trace — ordered list capturing
    # exact layer sequence, sizes, and injection status for each run.
    # Enables post-hoc analysis: info loss vs ordering instability.
    prompt_assembly_order = [
        {"name": "header", "chars": layer_sizes["header"], "injected": True},
        {"name": "role", "chars": layer_sizes["role"], "injected": layer_sizes["role"] > 0},
        {"name": "rules", "chars": layer_sizes["rules"], "injected": layer_sizes["rules"] > 0},
        {"name": "task_plan", "chars": layer_sizes["task_plan"], "injected": layer_sizes["task_plan"] > 0},
        {"name": "skills", "chars": layer_sizes["skills"], "injected": bool(skills)},
        {"name": "compaction_context", "chars": layer_sizes["compaction_context"], "injected": bool(compaction_ctx)},
    ]

    diagnostics = {
        "system_prompt_chars": len(prompt),
        "system_prompt_layers": layer_sizes,
        "prompt_assembly_order": prompt_assembly_order,
        "task_plan_injected": layer_sizes["task_plan"] > 0,
        "task_plan_chars": layer_sizes["task_plan"],
        "skills_injected": bool(skills),
        "skills_count": len(loaded_skills) if loaded_skills else 0,
        "compaction_context_injected": bool(compaction_ctx),
    }

    return prompt, diagnostics


def _debug_prompt_layers(agent_id: str, layers: list[str]) -> None:
    """Print prompt layer summary when DEBUG=true."""
    total = sum(len(l) for l in layers)
    names = ["Runtime Header", "Role Prompt", "Rules", "Skills Metadata", "Compaction Context"]
    print(f"[prompt] agent={agent_id} layers={len(layers)} chars={total}")
    for i, layer in enumerate(layers):
        label = names[i] if i < len(names) else f"Layer {i}"
        print(f"  [{label}] {len(layer)} chars")


# ── Skill content fetcher ──────────────────────────────────────────────────


def _fetch_user_installed_skill_metadata(user_root: str) -> list[dict] | None:
    """Scan user_root/skills/* for installed skill metadata (Phase M1).

    Reads frontmatter from each SKILL.md on disk.  This replaces the old
    DB-centric ``_fetch_loaded_skill_metadata`` so that *every* session
    automatically sees the full installed skill catalog — not only skills
    that were previously ``skill load``-ed.

    Returns a list of metadata dicts (name/version/description/tags),
    or None if no skills are found.
    """
    from workspace.manager import get_skills_dir
    from skills.package import parse_frontmatter

    try:
        skills_dir = Path(get_skills_dir(user_root))
        if not skills_dir.is_dir():
            return None

        metadata_list: list[dict] = []
        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            content = skill_md.read_text(encoding="utf-8")
            meta = parse_frontmatter(content)
            name = meta.get("name", child.name)
            if not name:
                continue
            metadata_list.append({
                "name": name,
                "version": meta.get("version", "0.1.0"),
                "description": meta.get("description", ""),
                "tags": meta.get("tags", []),
            })
        return metadata_list if metadata_list else None
    except Exception:
        return None


# ── HITL middleware configuration (§8.2) ───────────────────────────────────

_HITL_INTERRUPT_ON: dict[str, bool] = {
    "bash": True,
    "file_write": True,
    "file_edit": True,
    # file_read, file_inspect, skill, list_dir, glob, grep are NOT listed → auto-approved
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
        stream_usage=True,
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

    if settings.debug:
        tool_names = [t.name for t in tools]
        print(f"[build_agent] session={session_id[:8]} tools={len(tools)}: {tool_names}")

    # 3. System prompt (layered assembly)
    # Phase M1: scan user's installed skills on disk for stable prompt metadata.
    # Full SKILL.md content lives in conversation flow via skill load ToolMessages.
    loaded_skills = _fetch_user_installed_skill_metadata(user_root)

    system_prompt, prompt_diagnostics = build_system_prompt(
        agent_id=agent_id,
        session_dir=session_dir,
        user_root=user_root,
        model_id=model_id,
        session_id=session_id,
        loaded_skills=loaded_skills,
    )

    # 4. Middleware (FSD mode disables HITL interrupts)
    # Phase N1: SummarizationMiddleware removed — AgentD-native compaction replaces it.
    hitl = _build_hitl_middleware(session_dir)

    # 5. Checkpointer
    checkpointer = await get_checkpointer()

    # 6. Create agent
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[hitl],
        checkpointer=checkpointer,
    )

    # Phase L: attach diagnostics to agent for executor to record
    agent._prompt_diagnostics = prompt_diagnostics
    # Phase L §12.5: context window truth for usage ratio diagnostics
    agent._context_window_limit = resolved.context_window
    # Phase N1: attach session metadata for auto-compaction in executor
    agent._session_dir = session_dir
    agent._model_id = model_id

    return agent
