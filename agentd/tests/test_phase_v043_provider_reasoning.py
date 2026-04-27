"""Phase v0.4.3 — provider reasoning adapter tests."""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.messages import HumanMessage, ToolMessage


class TestProviderReasoningHelpers:
    def test_build_chatopenai_reasoning_kwargs_uses_extra_params(self):
        from agent.provider_reasoning import build_chatopenai_reasoning_kwargs
        from model_config.service import ResolvedModelConfig

        resolved = ResolvedModelConfig(
            source="db_default",
            name="DeepSeek",
            base_url="https://example.com/v1",
            api_key="sk-test",
            model_id="deepseek-v4",
            timeout_seconds=45,
            extra_params={
                "model_kwargs": {"reasoning_effort": "high"},
                "extra_body": {"thinking": {"type": "enabled"}},
                "enable_thinking": True,
            },
        )

        kwargs = build_chatopenai_reasoning_kwargs(resolved)

        assert kwargs["timeout"] == 45
        assert kwargs["model_kwargs"]["reasoning_effort"] == "high"
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_extract_reasoning_from_message_reads_think_tags(self):
        from agent.provider_reasoning import extract_reasoning_from_message

        message = AIMessage(content="<think>first pass</think>Final answer")

        extracted = extract_reasoning_from_message(message)

        assert extracted.visible_text == "first pass"
        assert extracted.source == "think_tag"
        assert extracted.provider_state is None

    def test_extract_reasoning_from_message_reads_reasoning_content(self):
        from agent.provider_reasoning import extract_reasoning_from_message

        chunk = AIMessageChunk(
            content="Visible text",
            additional_kwargs={"reasoning_content": "hidden chain"},
        )

        extracted = extract_reasoning_from_message(chunk)

        assert extracted.visible_text == "hidden chain"
        assert extracted.provider_state == {"reasoning_content": "hidden chain"}
        assert "reasoning_content" in extracted.source

    def test_extract_reasoning_from_message_reads_nested_delta_reasoning(self):
        from agent.provider_reasoning import extract_reasoning_from_message

        chunk = AIMessageChunk(
            content="",
            response_metadata={"delta": {"reasoning_content": "delta trace"}},
        )

        extracted = extract_reasoning_from_message(chunk)

        assert extracted.visible_text == "delta trace"
        assert extracted.provider_state == {"reasoning_content": "delta trace"}
        assert "delta:reasoning_content" in extracted.source

    def test_executor_reasoning_helper_accepts_ai_message_side_channel(self):
        from agent.executor import _extract_reasoning

        message = AIMessage(
            content="Answer",
            additional_kwargs={"reasoning_content": "provider side-channel"},
        )

        assert _extract_reasoning(message) == "provider side-channel"

    def test_resolve_provider_family_uses_heuristics(self):
        from agent.provider_reasoning import resolve_provider_family

        assert resolve_provider_family("openai_compatible", "https://api.deepseek.com/v1", "DeepSeek-V4") == "deepseek"
        assert resolve_provider_family("openai_compatible", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-max") == "qwen"
        assert resolve_provider_family("openai_compatible", "https://open.bigmodel.cn/api/paas/v4", "glm-5") == "glm"

    def test_build_chatopenai_reasoning_kwargs_normalizes_deepseek_thinking_alias(self):
        from agent.provider_reasoning import build_chatopenai_reasoning_kwargs
        from model_config.service import ResolvedModelConfig

        resolved = ResolvedModelConfig(
            source="db_default",
            name="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            model_id="deepseek-v4",
            extra_params={"enable_thinking": True, "reasoning_effort": "medium"},
        )

        kwargs = build_chatopenai_reasoning_kwargs(resolved)

        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kwargs["model_kwargs"]["reasoning_effort"] == "medium"

    def test_provider_chat_payload_preserves_reasoning_content_for_deepseek(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage, HumanMessage

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )
        payload = model._get_request_payload([
            HumanMessage(content="hi"),
            AIMessage(
                content="previous answer",
                additional_kwargs={"reasoning_content": "carry this forward"},
            ),
        ])

        assert payload["messages"][1]["role"] == "assistant"
        assert payload["messages"][1]["reasoning_content"] == "carry this forward"

    def test_provider_payload_rejects_invalid_tool_adjacency_by_default(self):
        from agent.provider_reasoning import (
            ProviderAwareChatOpenAI,
            TranscriptIntegrityError,
        )
        from langchain_core.messages import AIMessage, HumanMessage

        model = ProviderAwareChatOpenAI(
            model="gpt-test",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
        )

        with pytest.raises(TranscriptIntegrityError) as exc_info:
            model._get_request_payload([
                HumanMessage(content="write a file"),
                AIMessage(
                    content="",
                    additional_kwargs={
                        "tool_calls": [{
                            "id": "call_missing",
                            "type": "function",
                            "function": {"name": "file_write", "arguments": "{}"},
                        }],
                    },
                ),
            ])

        assert exc_info.value.code == "TRANSCRIPT_INTEGRITY_ERROR"
        assert exc_info.value.issues[0]["missing_tool_call_ids"] == ["call_missing"]

    def test_provider_payload_sanitizer_requires_explicit_fallback_flag(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage, HumanMessage

        model = ProviderAwareChatOpenAI(
            model="gpt-test",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            strict_provider_compat_fallback=True,
        )
        payload = model._get_request_payload([
            HumanMessage(content="write a file"),
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [{
                        "id": "call_missing",
                        "type": "function",
                        "function": {"name": "file_write", "arguments": "{}"},
                    }],
                },
            ),
        ])

        assert len(payload["messages"]) == 1

    def test_provider_payload_accepts_complete_tool_group(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        model = ProviderAwareChatOpenAI(
            model="gpt-test",
            api_key="sk-test",
            base_url="https://api.example.com/v1",
        )
        payload = model._get_request_payload([
            HumanMessage(content="write a file"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_write",
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
            ),
            ToolMessage(
                content="done",
                name="file_write",
                tool_call_id="call_write",
            ),
        ])

        assert [message["role"] for message in payload["messages"]] == [
            "user",
            "assistant",
            "tool",
        ]
        assert payload["messages"][2]["tool_call_id"] == "call_write"

    def test_provider_payload_rejects_old_dangling_group_even_with_latest_complete_pair(self):
        from agent.provider_reasoning import (
            ProviderAwareChatOpenAI,
            TranscriptIntegrityError,
        )
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )

        with pytest.raises(TranscriptIntegrityError) as exc_info:
            model._get_request_payload([
                HumanMessage(content="old turn"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_old",
                        "name": "bash",
                        "args": {},
                    }],
                ),
                HumanMessage(content="latest turn"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_zip",
                        "name": "bash",
                        "args": {},
                    }],
                ),
                ToolMessage(
                    content="adding: report.xlsx",
                    name="bash",
                    tool_call_id="call_zip",
                ),
            ])

        assert exc_info.value.issues[0]["index"] == 1
        assert exc_info.value.issues[0]["missing_tool_call_ids"] == ["call_old"]

    def test_provider_payload_fallback_drops_old_dangling_group_and_keeps_latest_pair(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
            strict_provider_compat_fallback=True,
        )
        payload = model._get_request_payload([
            HumanMessage(content="old turn"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_old",
                    "name": "bash",
                    "args": {},
                }],
            ),
            HumanMessage(content="latest turn"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call_zip",
                    "name": "bash",
                    "args": {},
                }],
            ),
            ToolMessage(
                content="adding: report.xlsx",
                name="bash",
                tool_call_id="call_zip",
            ),
        ])

        assert [message["role"] for message in payload["messages"]] == [
            "user",
            "user",
            "assistant",
            "tool",
        ]
        assert payload["messages"][2]["tool_calls"][0]["id"] == "call_zip"
        assert payload["messages"][3]["tool_call_id"] == "call_zip"

    def test_provider_payload_drops_orphan_tool_call_group_when_fallback_enabled(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage, HumanMessage

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
            strict_provider_compat_fallback=True,
        )
        payload = model._get_request_payload([
            HumanMessage(content="launch child"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_child", "name": "launch_subagent", "args": {}}],
            ),
            AIMessage(content="[Sub-task completed]\nsummary"),
            HumanMessage(content="continue"),
        ])

        assert [message["role"] for message in payload["messages"]] == [
            "user",
            "assistant",
            "user",
        ]
        assert "tool_calls" not in payload["messages"][1]

    def test_provider_payload_converts_internal_subtask_bridge_to_user_context(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessage

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )
        payload = model._get_request_payload([
            AIMessage(
                content="[Sub-task completed]\nsummary",
                additional_kwargs={"agentd_internal": "subtask_result_bridge"},
            ),
        ])

        assert payload["messages"] == [{
            "role": "user",
            "content": "[Sub-task completed]\nsummary",
        }]

    def test_provider_chat_result_preserves_deepseek_reasoning_content(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )
        result = model._create_chat_result({
            "id": "chatcmpl-test",
            "model": "deepseek-v4",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "must carry through",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "skill", "arguments": "{\"action\":\"list\"}"},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

        message = result.generations[0].message

        assert message.additional_kwargs["reasoning_content"] == "must carry through"
        assert message.tool_calls[0]["id"] == "call_1"

    def test_provider_stream_chunk_preserves_deepseek_reasoning_delta(self):
        from agent.provider_reasoning import ProviderAwareChatOpenAI
        from langchain_core.messages import AIMessageChunk

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )
        generation_chunk = model._convert_chunk_to_generation_chunk(
            {
                "choices": [{
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": "stream delta",
                    },
                    "finish_reason": None,
                }],
            },
            AIMessageChunk,
            {},
        )

        assert generation_chunk.message.additional_kwargs["reasoning_content"] == "stream delta"

    def test_streaming_reasoning_content_accumulates_before_payload(self):
        from agent.provider_reasoning import (
            ProviderAwareChatOpenAI,
            append_provider_state_delta,
            extract_reasoning_from_message,
            merge_provider_state_final,
        )
        from langchain_core.messages import AIMessage, HumanMessage

        state = {}
        for chunk in (
            AIMessageChunk(content="", additional_kwargs={"reasoning_content": "part1"}),
            AIMessageChunk(content="", additional_kwargs={"reasoning_content": "part2"}),
        ):
            append_provider_state_delta(state, extract_reasoning_from_message(chunk).provider_state)

        tool_call_message = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "skill", "arguments": "{\"action\":\"list\"}"},
                }],
            },
        )
        merged_kwargs = dict(tool_call_message.additional_kwargs)
        merge_provider_state_final(merged_kwargs, state)
        tool_call_message.additional_kwargs = merged_kwargs

        model = ProviderAwareChatOpenAI(
            model="deepseek-v4",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            provider_family="deepseek",
        )
        payload = model._get_request_payload([
            HumanMessage(content="use a tool"),
            tool_call_message,
            ToolMessage(content="[]", tool_call_id="call_1"),
        ])

        assert tool_call_message.additional_kwargs["reasoning_content"] == "part1part2"
        assert payload["messages"][1]["reasoning_content"] == "part1part2"
        assert payload["messages"][1]["tool_calls"][0]["id"] == "call_1"

    def test_final_reasoning_state_overrides_stream_delta(self):
        from agent.provider_reasoning import append_provider_state_delta, merge_provider_state_final

        state = {}
        append_provider_state_delta(state, {"reasoning_content": "partial"})
        merge_provider_state_final(state, {"reasoning_content": "complete"})

        assert state == {"reasoning_content": "complete"}

    def test_checkpoint_tool_adjacency_accepts_complete_tool_group(self):
        from agent.executor import _find_invalid_tool_adjacency_indices

        messages = [
            HumanMessage(content="run tools"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_a", "name": "list_dir", "args": {}},
                    {"id": "call_b", "name": "knowledge_catalog", "args": {}},
                ],
            ),
            ToolMessage(content="ok", tool_call_id="call_a"),
            ToolMessage(content="ok", tool_call_id="call_b"),
            AIMessage(content="done"),
        ]

        assert _find_invalid_tool_adjacency_indices(messages) == []

    def test_checkpoint_tool_adjacency_rejects_subagent_orphan_group(self):
        from agent.executor import _find_invalid_tool_adjacency_indices

        messages = [
            HumanMessage(content="launch child"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_search", "name": "knowledge_search", "args": {}},
                    {"id": "call_child", "name": "launch_subagent", "args": {}},
                ],
            ),
            AIMessage(content="[Sub-task completed]\nsummary"),
            HumanMessage(content="[Subtask Continuation - internal only]"),
        ]

        assert _find_invalid_tool_adjacency_indices(messages) == [1]

    def test_missing_tail_tool_messages_repairs_waiting_boundary(self):
        from agent.executor import _missing_tail_tool_messages

        messages = [
            HumanMessage(content="launch child"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_search", "name": "knowledge_search", "args": {}},
                    {"id": "call_child", "name": "launch_subagent", "args": {}},
                ],
            ),
        ]
        search_result = ToolMessage(content="search result", tool_call_id="call_search")
        child_result = ToolMessage(content="waiting", tool_call_id="call_child")

        assert _missing_tail_tool_messages(messages, [child_result, search_result]) == [
            search_result,
            child_result,
        ]

    def test_candidate_tool_group_patch_preserves_ai_tool_pair(self):
        from agent.executor import _candidate_tool_group_patch

        ai_message = AIMessage(
            content="",
            id="ai-1",
            tool_calls=[
                {"id": "call_child", "name": "launch_subagent", "args": {}},
            ],
        )
        tool_result = ToolMessage(content="waiting", tool_call_id="call_child")

        patch = _candidate_tool_group_patch(
            [HumanMessage(content="launch child"), ai_message],
            ai_message,
            [tool_result],
        )

        assert patch == [ai_message, tool_result]

    def test_provider_chat_payload_maps_qwen_enable_thinking(self):
        from agent.provider_reasoning import build_chatopenai_reasoning_kwargs
        from model_config.service import ResolvedModelConfig

        resolved = ResolvedModelConfig(
            source="db_default",
            name="Qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test",
            model_id="qwen-max",
            extra_params={"thinking": {"type": "enabled", "budget": 2048}},
        )

        kwargs = build_chatopenai_reasoning_kwargs(resolved)

        assert kwargs["extra_body"]["enable_thinking"] is True
        assert kwargs["model_kwargs"]["thinking_budget"] == 2048

    def test_provider_chat_payload_maps_glm_enable_thinking(self):
        from agent.provider_reasoning import build_chatopenai_reasoning_kwargs
        from model_config.service import ResolvedModelConfig

        resolved = ResolvedModelConfig(
            source="db_default",
            name="GLM",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="sk-test",
            model_id="glm-5",
            extra_params={"enable_thinking": False},
        )

        kwargs = build_chatopenai_reasoning_kwargs(resolved)

        assert kwargs["model_kwargs"]["thinking"] == {"type": "disabled"}
