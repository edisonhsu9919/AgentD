"""Host-handled session slash commands."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy import update as sa_update

from agent.runtime import build_agent
from auth.models import User
from session import service as session_svc
from session.models import Session
from tools.base import ToolContext
from tools.skill import SkillTool
from workspace.manager import get_session_dir


@dataclass(frozen=True)
class ParsedSessionCommand:
    kind: str
    action: str
    name: str
    normalized_command: str


@dataclass(frozen=True)
class SessionCommandResult:
    command: str
    status: str
    message: Any
    loaded_skills: list[dict[str, str]]


class SessionCommandError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_COMMAND") -> None:
        self.code = code
        super().__init__(message)


def parse_session_command(command: str) -> ParsedSessionCommand:
    raw = (command or "").strip()
    match = re.fullmatch(r"/skill\s+load\s+(.+)", raw, flags=re.IGNORECASE)
    if not match:
        if raw.lower().startswith("/skill load"):
            raise SessionCommandError(
                "Skill name is required. Use /skill load <skill-name>.",
                code="MISSING_SKILL_NAME",
            )
        raise SessionCommandError(
            "Unsupported command. Supported command: /skill load <skill-name>.",
            code="UNSUPPORTED_COMMAND",
        )

    tool = SkillTool()
    name = tool._normalize_skill_name(match.group(1))
    if not isinstance(name, str) or not name.strip():
        raise SessionCommandError(
            "Skill name is required. Use /skill load <skill-name>.",
            code="MISSING_SKILL_NAME",
        )
    name = name.strip()
    if any(sep in name for sep in ("/", "\\")) or name in {".", ".."}:
        raise SessionCommandError(
            "Skill name must be the bare installed skill name.",
            code="INVALID_SKILL_NAME",
        )

    return ParsedSessionCommand(
        kind="skill",
        action="load",
        name=name,
        normalized_command=f"/skill load {name}",
    )


async def execute_session_command(
    db,
    *,
    session: Session,
    current_user: User,
    command: str,
) -> SessionCommandResult:
    parsed = parse_session_command(command)
    if parsed.kind == "skill" and parsed.action == "load":
        return await _execute_skill_load_command(
            db,
            session=session,
            current_user=current_user,
            parsed=parsed,
        )
    raise SessionCommandError(
        "Unsupported command. Supported command: /skill load <skill-name>.",
        code="UNSUPPORTED_COMMAND",
    )


async def _execute_skill_load_command(
    db,
    *,
    session: Session,
    current_user: User,
    parsed: ParsedSessionCommand,
) -> SessionCommandResult:
    session_id = str(session.id)
    session_dir = get_session_dir(current_user.workspace, session_id)
    ctx = ToolContext(
        user_id=str(current_user.id),
        session_id=session_id,
        user_root=current_user.workspace,
        session_dir=session_dir,
        workspace_dir=session_dir,
        venv_bin=current_user.workspace.rstrip("/") + "/.venv/bin/",
        publish=None,
        run_id="",
    )

    tool_result = await SkillTool().execute(ctx, action="load", name=parsed.name)
    if tool_result.get("is_error"):
        raise SessionCommandError(
            str(tool_result.get("output") or "Skill load failed"),
            code="SKILL_LOAD_FAILED",
        )

    output = str(tool_result.get("output") or "")
    skill_name = str(tool_result.get("skill_name") or parsed.name)
    skill_version = str(tool_result.get("skill_version") or "0.1.0")
    entry = {"name": skill_name, "version": skill_version}

    loaded_skills = await _merge_loaded_skill(
        db,
        session=session,
        entry=entry,
    )
    await _increment_skill_usage(
        db,
        user_id=current_user.id,
        skill_name=skill_name,
        skill_version=skill_version,
    )

    message = await session_svc.create_message(
        db,
        session_id=session.id,
        role="user",
        parts=[
            {
                "type": "command",
                "command": parsed.normalized_command,
            },
            {
                "type": "command_result",
                "command": parsed.normalized_command,
                "status": "success",
                "text": output,
                "skill_name": skill_name,
                "skill_version": skill_version,
            },
        ],
    )

    await _append_command_context_to_checkpoint(
        session=session,
        current_user=current_user,
        session_dir=session_dir,
        command=parsed.normalized_command,
        text=output,
    )

    return SessionCommandResult(
        command=parsed.normalized_command,
        status="success",
        message=message,
        loaded_skills=loaded_skills,
    )


async def _merge_loaded_skill(
    db,
    *,
    session: Session,
    entry: dict[str, str],
) -> list[dict[str, str]]:
    existing: list[dict[str, str]] = []
    for item in session.loaded_skills or []:
        if isinstance(item, dict) and item.get("name"):
            existing.append({
                "name": str(item.get("name")),
                "version": str(item.get("version") or "0.1.0"),
            })
        elif isinstance(item, str) and item:
            existing.append({"name": item, "version": "0.1.0"})
    keys = {
        (str(item.get("name")), str(item.get("version", "0.1.0")))
        for item in existing
    }
    key = (entry["name"], entry["version"])
    if key not in keys:
        existing.append(entry)
    await session_svc.update_loaded_skills(db, session.id, existing)
    session.loaded_skills = existing
    return existing


async def _increment_skill_usage(
    db,
    *,
    user_id: uuid.UUID,
    skill_name: str,
    skill_version: str,
) -> None:
    try:
        from skills import service as skill_svc
        from skills import user_skill_service as us_svc
        from skills.models import Skill as SkillModel

        skill_record = await skill_svc.get_skill_by_name_version(
            db, skill_name, skill_version,
        )
        if skill_record:
            await db.execute(
                sa_update(SkillModel)
                .where(SkillModel.id == skill_record.id)
                .values(
                    usage_count=SkillModel.usage_count + 1,
                    last_used_at=datetime.now(timezone.utc),
                )
            )
        await us_svc.increment_usage(db, user_id, skill_name)
    except Exception:
        return


async def _append_command_context_to_checkpoint(
    *,
    session: Session,
    current_user: User,
    session_dir: str,
    command: str,
    text: str,
) -> None:
    agent = await build_agent(
        str(session.id),
        str(current_user.id),
        current_user.workspace,
        session_dir,
        session.agent_id,
        session.model_id,
        run_id="",
    )
    config = {"configurable": {"thread_id": str(session.id)}}
    message = HumanMessage(
        content=(
            "[Slash Command Context]\n"
            f"Command: {command}\n"
            "Status: success\n\n"
            f"{text}"
        ),
        additional_kwargs={
            "agentd_internal": "slash_skill_load_command",
            "origin": "slash_command",
        },
        id=str(uuid.uuid4()),
    )
    try:
        await agent.aupdate_state(
            config=config,
            values={"messages": [message]},
            as_node="__start__",
        )
    except TypeError:
        await agent.aupdate_state(config=config, values={"messages": [message]})
