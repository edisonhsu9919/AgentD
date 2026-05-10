"""v0.4.9 follow-up: host-handled /skill load command."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from skills.filesystem import get_skills_dir
from workspace.manager import ensure_user_root


def _create_skill(user_root: str, name: str, version: str = "1.2.3") -> None:
    skill_dir = os.path.join(get_skills_dir(user_root), name)
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(
            f"---\nname: {name}\nversion: {version}\ndescription: test\n---\n\n"
            "Follow this skill."
        )


def test_parse_skill_load_command_normalizes_quoted_name():
    from agent.session_commands import parse_session_command

    parsed = parse_session_command('  /skill   load   "pdf-rename"  ')

    assert parsed.kind == "skill"
    assert parsed.action == "load"
    assert parsed.name == "pdf-rename"
    assert parsed.normalized_command == "/skill load pdf-rename"


@pytest.mark.parametrize("command", ["/skill load", "/skill load   "])
def test_parse_skill_load_rejects_missing_name(command):
    from agent.session_commands import SessionCommandError, parse_session_command

    with pytest.raises(SessionCommandError) as exc:
        parse_session_command(command)

    assert exc.value.code == "MISSING_SKILL_NAME"


def test_parse_skill_load_rejects_unsupported_command():
    from agent.session_commands import SessionCommandError, parse_session_command

    with pytest.raises(SessionCommandError) as exc:
        parse_session_command("/skill reset")

    assert exc.value.code == "UNSUPPORTED_COMMAND"


@pytest.mark.asyncio
async def test_execute_skill_load_command_persists_message_and_checkpoint(tmp_path):
    from agent.session_commands import execute_session_command

    user_root = os.path.join(str(tmp_path), "user")
    ensure_user_root(user_root)
    _create_skill(user_root, "pdf-rename", version="2.0.0")

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status="idle",
        agent_id="assistant",
        model_id="model",
        loaded_skills=[],
    )
    user = SimpleNamespace(id=user_id, workspace=user_root)
    created_messages: list[dict] = []

    async def fake_create_message(_db, *, session_id, role, parts, **kwargs):
        message = SimpleNamespace(
            id=uuid.uuid4(),
            session_id=session_id,
            role=role,
            parts=parts,
            is_summary=False,
            token_usage=None,
            seq=1,
            created_at=None,
        )
        created_messages.append({"role": role, "parts": parts})
        return message

    fake_agent = SimpleNamespace(aupdate_state=AsyncMock())

    with (
        patch("agent.session_commands.session_svc.create_message", new=fake_create_message),
        patch("agent.session_commands.session_svc.update_loaded_skills", new=AsyncMock()),
        patch("agent.session_commands._increment_skill_usage", new=AsyncMock()),
        patch("agent.session_commands.build_agent", new=AsyncMock(return_value=fake_agent)),
    ):
        result = await execute_session_command(
            AsyncMock(),
            session=session,
            current_user=user,
            command="/skill load pdf-rename",
        )

    assert result.loaded_skills == [{"name": "pdf-rename", "version": "2.0.0"}]
    assert created_messages[0]["role"] == "user"
    assert created_messages[0]["parts"][0] == {
        "type": "command",
        "command": "/skill load pdf-rename",
    }
    assert created_messages[0]["parts"][1]["type"] == "command_result"
    assert "[Skill: pdf-rename v2.0.0]" in created_messages[0]["parts"][1]["text"]

    update = fake_agent.aupdate_state.await_args.kwargs
    assert update["as_node"] == "__start__"
    checkpoint_message = update["values"]["messages"][0]
    assert checkpoint_message.additional_kwargs["agentd_internal"] == "slash_skill_load_command"
    assert "[Skill: pdf-rename v2.0.0]" in checkpoint_message.content


@pytest.mark.asyncio
async def test_slash_command_context_is_not_repersisted():
    from agent.message_persistence import persist_messages
    from langchain_core.messages import HumanMessage

    create_message = AsyncMock()
    db = AsyncMock()
    db.commit = AsyncMock()

    with (
        patch("agent.message_persistence.AsyncSessionLocal") as db_ctx,
        patch("agent.message_persistence.session_svc.create_message", new=create_message),
        patch("agent.message_persistence.load_existing_part_keys", new=AsyncMock(return_value=set())),
    ):
        db_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await persist_messages(
            str(uuid.uuid4()),
            [
                HumanMessage(content="normal"),
                HumanMessage(
                    content="[Slash Command Context]\n...",
                    additional_kwargs={"agentd_internal": "slash_skill_load_command"},
                ),
            ],
        )

    create_message.assert_not_awaited()
