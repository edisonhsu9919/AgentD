"""v0.4.9 maintenance sidecar and session title contract tests."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError


def test_maintenance_chat_kwargs_disable_thinking_for_short_tasks():
    from agent.maintenance_model import ResolvedMaintenanceModelConfig, maintenance_chat_kwargs

    resolved = ResolvedMaintenanceModelConfig(
        purpose="title",
        source="runtime:vlm",
        name="VLM",
        base_url="http://provider.test",
        api_key="key",
        model_id="qwen-test",
        timeout_seconds=12,
    )

    kwargs = maintenance_chat_kwargs(resolved, purpose="title")

    assert kwargs["max_tokens"] == 256
    assert kwargs["temperature"] == 0.2
    assert kwargs["timeout"] == 12
    assert kwargs["top_p"] == 0.8
    assert kwargs["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False,
        "preserve_thinking": False,
    }
    assert kwargs["extra_body"]["enable_thinking"] is False
    assert kwargs["extra_body"]["preserve_thinking"] is False


def test_maintenance_chat_kwargs_preserve_db_extra_params():
    from agent.maintenance_model import ResolvedMaintenanceModelConfig, maintenance_chat_kwargs

    resolved = ResolvedMaintenanceModelConfig(
        purpose="title",
        source="db_default:vlm",
        name="VLM",
        base_url="http://provider.test",
        api_key="key",
        model_id="qwen-test",
        extra_params={
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "repeat_penalty": 1.0,
            "chat_template_kwargs": {
                "enable_thinking": False,
                "preserve_thinking": False,
            },
        },
    )

    kwargs = maintenance_chat_kwargs(resolved, purpose="title", max_tokens=512)

    assert kwargs["temperature"] == 0.7
    assert kwargs["extra_body"]["top_k"] == 20
    assert kwargs["extra_body"]["min_p"] == 0.0
    assert kwargs["extra_body"]["repeat_penalty"] == 1.0
    assert kwargs["max_tokens"] == 512
    assert kwargs["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert "min_p" not in kwargs.get("model_kwargs", {})


@pytest.mark.asyncio
async def test_invoke_maintenance_chat_scopes_http_client_lifecycle():
    from agent.maintenance_model import (
        ResolvedMaintenanceModelConfig,
        invoke_maintenance_chat,
    )

    resolved = ResolvedMaintenanceModelConfig(
        purpose="title",
        source="runtime:vlm",
        name="VLM",
        base_url="http://provider.test",
        api_key="key",
        model_id="qwen-test",
    )
    fake_result = MagicMock()
    fake_client = object()

    with patch("agent.maintenance_model.resolve_maintenance_model_config", new_callable=AsyncMock) as mock_resolve, \
         patch("agent.maintenance_model.httpx.AsyncClient") as mock_client_cls, \
         patch("agent.maintenance_model.ProviderAwareChatOpenAI") as mock_chat_cls:
        mock_resolve.return_value = resolved
        mock_client_ctx = MagicMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_ctx

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = fake_result
        mock_chat_cls.return_value = mock_llm

        result, actual_resolved = await invoke_maintenance_chat(
            AsyncMock(),
            purpose="title",
            messages=[HumanMessage(content="hello")],
        )

    assert result is fake_result
    assert actual_resolved is resolved
    assert mock_chat_cls.call_args.kwargs["http_async_client"] is fake_client
    mock_client_ctx.__aexit__.assert_awaited_once()


def test_session_title_sanitizer_strips_reasoning_tags_and_newlines():
    from agent.session_title import sanitize_session_title

    title = sanitize_session_title(
        '<think>reasoning</think>\n标题： "  保险条款对比\n结果  "',
    )

    assert title == "保险条款对比 结果"


def test_title_excerpt_from_langchain_messages():
    from agent.session_title import _conversation_excerpt

    excerpt = _conversation_excerpt([
        HumanMessage(content="请用一句话回答：AgentD 是什么？"),
        AIMessage(content="<think>分析</think>AgentD 是企业任务工作台。"),
    ])

    assert "User: 请用一句话回答：AgentD 是什么" in excerpt
    assert "Assistant: AgentD 是企业任务工作台" in excerpt
    assert "think" not in excerpt


def test_title_excerpt_from_api_message_dict_parts():
    from agent.session_title import _conversation_excerpt

    excerpt = _conversation_excerpt([
        {
            "role": "user",
            "parts": [
                {"type": "text", "content": "请给这次会话命名"},
                {"type": "tool_result", "output": "ignored"},
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "ignored reasoning"},
                {"type": "text", "content": "可以命名为维护侧车修复。"},
                {"type": "source_refs", "sources": []},
            ],
        },
    ])

    assert "User: 请给这次会话命名" in excerpt
    assert "Assistant: 可以命名为维护侧车修复" in excerpt
    assert "ignored" not in excerpt


def test_session_update_schema_rejects_empty_or_long_title():
    from session.schemas import SessionUpdate

    assert SessionUpdate(title="  手动\n标题  ").title == "手动 标题"

    with pytest.raises(ValidationError):
        SessionUpdate(title=" \n ")

    with pytest.raises(ValidationError):
        SessionUpdate(title="x" * 81)


def test_session_patch_route_is_available_for_manual_title_updates():
    from session.router import router

    routes = {
        (route.path, tuple(sorted(getattr(route, "methods", set()))))
        for route in router.routes
    }

    assert ("/{session_id}", ("PATCH",)) in routes


@pytest.mark.asyncio
async def test_generate_session_title_uses_mechanical_fallback_when_content_empty():
    from agent.session_title import DEFAULT_TITLE, generate_session_title

    sid = uuid.uuid4()
    session = MagicMock()
    session.title = DEFAULT_TITLE
    empty_result = MagicMock()
    empty_result.content = ""
    empty_result.response_metadata = {"finish_reason": "stop"}

    with patch("agent.session_title.AsyncSessionLocal") as mock_db_ctx, \
         patch("agent.session_title.session_svc.get_session", new_callable=AsyncMock) as mock_get_session, \
         patch("agent.session_title.invoke_maintenance_chat", new_callable=AsyncMock) as mock_invoke, \
         patch("agent.session_title._record_title_diagnostics_in_db", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_get_session.return_value = session
        mock_invoke.return_value = (
            empty_result,
            MagicMock(source="runtime:vlm", model_id="qwen-test"),
        )

        result = await generate_session_title(
            str(sid),
            [HumanMessage(content="请帮我比较保险条款责任范围")],
        )

    assert result.title == "请帮我比较保险条款责任范围"
    assert result.diagnostics["maintenance_title_fallback_used"] is True
    assert result.diagnostics["maintenance_title_content_empty"] is True
    assert result.diagnostics["maintenance_title_model_source"] == "runtime:vlm"


@pytest.mark.asyncio
async def test_generate_session_title_timeout_uses_mechanical_fallback():
    from agent.session_title import DEFAULT_TITLE, generate_session_title

    sid = uuid.uuid4()
    session = MagicMock()
    session.title = DEFAULT_TITLE

    with patch("agent.session_title.AsyncSessionLocal") as mock_db_ctx, \
         patch("agent.session_title.session_svc.get_session", new_callable=AsyncMock) as mock_get_session, \
         patch("agent.session_title.invoke_maintenance_chat", new_callable=AsyncMock) as mock_invoke, \
         patch("agent.session_title._record_title_diagnostics_in_db", new_callable=AsyncMock):
        mock_db = AsyncMock()
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_get_session.return_value = session
        mock_invoke.side_effect = asyncio.TimeoutError

        result = await generate_session_title(
            str(sid),
            [HumanMessage(content="请总结这次 AgentD 标题维护测试")],
        )

    assert result.title == "请总结这次 AgentD 标题维护测试"
    assert result.diagnostics["maintenance_title_fallback_used"] is True
    assert result.diagnostics["maintenance_title_content_empty"] is True
    assert result.diagnostics["maintenance_title_error"].startswith("title_model_timeout:")


@pytest.mark.asyncio
async def test_generate_session_title_does_not_overwrite_manual_title():
    from agent.session_title import generate_session_title

    session = MagicMock()
    session.title = "手动标题"

    with patch("agent.session_title.AsyncSessionLocal") as mock_db_ctx, \
         patch("agent.session_title.session_svc.get_session", new_callable=AsyncMock) as mock_get_session, \
         patch("agent.session_title.invoke_maintenance_chat", new_callable=AsyncMock) as mock_invoke:
        mock_db = AsyncMock()
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_get_session.return_value = session

        result = await generate_session_title(
            str(uuid.uuid4()),
            [HumanMessage(content="请生成一个新标题")],
        )

    assert result.title is None
    assert result.diagnostics["maintenance_title_status"] == "skipped"
    assert mock_invoke.await_count == 0


@pytest.mark.asyncio
async def test_finalize_awaits_title_maintenance_after_done(monkeypatch):
    from agent import executor
    from agent.runtime_integrity import RuntimeGateAction, RuntimeGateDecision

    messages = [
        HumanMessage(content="hello"),
        AIMessage(content="done"),
    ]
    snapshot = SimpleNamespace(values={"messages": messages})

    class FakeAgent:
        async def aget_state(self, _config):
            return snapshot

    agent = FakeAgent()
    agent._user_id = "user-1"
    agent._user_root = "/tmp/user"
    agent._microcompact_result = None

    monkeypatch.setattr(executor, "_persist_messages", AsyncMock())
    monkeypatch.setattr(executor, "_persist_loaded_skills", AsyncMock())
    monkeypatch.setattr(executor, "_record_run_diagnostics", AsyncMock())
    monkeypatch.setattr(executor, "_update_db_status", AsyncMock())
    monkeypatch.setattr(
        executor,
        "_decide_runtime_terminal_state",
        AsyncMock(return_value=(
            RuntimeGateDecision(
                action=RuntimeGateAction.FINALIZE_IDLE,
                reason="clean",
                can_accept_user_prompt=True,
            ),
            None,
        )),
    )
    monkeypatch.setattr(executor, "_maybe_generate_title", AsyncMock())
    monkeypatch.setattr(executor, "_update_session_memory_async", AsyncMock())

    publish = AsyncMock()
    await executor._finalize(agent, {}, str(uuid.uuid4()), publish)

    payloads = [call.args[1] for call in publish.await_args_list]
    done_index = next(i for i, payload in enumerate(payloads) if payload.get("event") == "done")
    assert done_index >= 0
    executor._maybe_generate_title.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_generate_title_records_publish_diagnostics(monkeypatch):
    from agent import executor

    async def fake_generate(_session_id, _messages):
        return SimpleNamespace(
            title="自动标题",
            diagnostics={"maintenance_title_status": "updated"},
        )

    record = AsyncMock()
    monkeypatch.setattr("agent.session_title.generate_session_title", fake_generate)
    monkeypatch.setattr("agent.session_title.record_title_generation_diagnostics", record)

    async def publish(_session_id, event):
        event["_event_bridge_notify_ok"] = True

    await executor._maybe_generate_title(
        str(uuid.uuid4()),
        [HumanMessage(content="hello")],
        publish,
    )

    diagnostics = record.await_args.args[1]
    assert diagnostics["maintenance_title_event_publish_attempted"] is True
    assert diagnostics["maintenance_title_event_publish_ok"] is True


@pytest.mark.asyncio
async def test_maybe_generate_title_records_skip_diagnostics(monkeypatch):
    from agent import executor

    async def fake_generate(_session_id, _messages):
        return SimpleNamespace(
            title=None,
            diagnostics={
                "maintenance_title_status": "skipped",
                "maintenance_title_skip_reason": "empty_conversation_excerpt",
            },
        )

    record = AsyncMock()
    monkeypatch.setattr("agent.session_title.generate_session_title", fake_generate)
    monkeypatch.setattr("agent.session_title.record_title_generation_diagnostics", record)

    await executor._maybe_generate_title(
        str(uuid.uuid4()),
        [{"role": "user", "parts": [{"type": "tool_result", "output": "ignored"}]}],
        AsyncMock(),
    )

    diagnostics = record.await_args.args[1]
    assert diagnostics["maintenance_title_status"] == "skipped"
    assert diagnostics["maintenance_title_skip_reason"] == "empty_conversation_excerpt"


@pytest.mark.asyncio
async def test_maybe_generate_title_records_publish_failure(monkeypatch):
    from agent import executor

    async def fake_generate(_session_id, _messages):
        return SimpleNamespace(
            title="自动标题",
            diagnostics={"maintenance_title_status": "updated"},
        )

    record = AsyncMock()
    monkeypatch.setattr("agent.session_title.generate_session_title", fake_generate)
    monkeypatch.setattr("agent.session_title.record_title_generation_diagnostics", record)

    async def publish(_session_id, _event):
        raise RuntimeError("notify failed")

    await executor._maybe_generate_title(
        str(uuid.uuid4()),
        [HumanMessage(content="hello")],
        publish,
    )

    diagnostics = record.await_args.args[1]
    assert diagnostics["maintenance_title_event_publish_attempted"] is True
    assert diagnostics["maintenance_title_event_publish_ok"] is False
    assert "notify failed" in diagnostics["maintenance_title_event_publish_error"]
