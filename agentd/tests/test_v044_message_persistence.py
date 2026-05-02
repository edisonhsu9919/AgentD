"""v0.4.4 Phase D message persistence helper tests."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.message_persistence import (
    build_persistable_message_parts,
    extract_knowledge_source_refs,
    part_dedupe_keys,
)


def test_part_dedupe_keys_preserve_tool_and_runtime_keys():
    keys = part_dedupe_keys({
        "type": "tool_result",
        "tool_call_id": "call_1",
        "runtime_message_id": "runtime-1",
    })

    assert keys == [
        "runtime:runtime-1:tool_result:call_1",
        "tool:tool_result:call_1",
    ]


def test_ai_message_parts_preserve_reasoning_text_and_tool_calls():
    message = AIMessage(
        content="<think>trace</think>Visible",
        id="ai-1",
        tool_calls=[{"id": "call_1", "name": "bash", "args": {"cmd": "ls"}}],
    )

    role, parts, is_summary = build_persistable_message_parts(message)

    assert role == "assistant"
    assert is_summary is False
    assert parts[0]["type"] == "reasoning"
    assert parts[0]["content"] == "trace"
    assert parts[1]["type"] == "text"
    assert parts[1]["content"] == "Visible"
    assert parts[2]["type"] == "tool_call"
    assert parts[2]["runtime_message_id"] == "ai-1"


def test_tool_message_part_preserves_error_status():
    message = ToolMessage(
        content="bad",
        tool_call_id="call_1",
        name="bash",
        status="error",
    )

    role, parts, _ = build_persistable_message_parts(message)

    assert role == "tool"
    assert parts == [{
        "type": "tool_result",
        "tool_call_id": "call_1",
        "tool_name": "bash",
        "output": "bad",
        "is_error": True,
    }]


def test_human_context_summary_is_summary():
    role, parts, is_summary = build_persistable_message_parts(
        HumanMessage(content="[Context Summary]\nold")
    )

    assert role == "user"
    assert parts[0]["content"].startswith("[Context Summary]")
    assert is_summary is True


def test_extract_knowledge_source_refs_dedupes_search_and_read():
    messages = [
        ToolMessage(
            content='{"results":[{"doc_id":"d1","title":"Doc","kind":"md","excerpts":[{"text":"abc"}]}]}',
            tool_call_id="call_search",
            name="knowledge_search",
        ),
        ToolMessage(
            content='{"doc_id":"d1","title":"Doc","kind":"md","source_file":"doc.md","content":"abcdef"}',
            tool_call_id="call_read",
            name="knowledge_read",
        ),
    ]

    refs = extract_knowledge_source_refs(messages)

    assert refs == [{
        "doc_id": "d1",
        "title": "Doc",
        "kind": "md",
        "source_file": "doc.md",
        "evidence_excerpt": "abc",
        "ref_index": 1,
    }]
