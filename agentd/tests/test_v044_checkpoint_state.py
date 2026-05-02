"""v0.4.4 Phase A checkpoint state classifier tests."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.checkpoint_state import (
    CheckpointStateClassifier,
    CheckpointStateKind,
    classify_checkpoint,
    classify_checkpoint_snapshot,
)


def test_empty_messages_classify_empty():
    state = classify_checkpoint([])

    assert state.state_kind == CheckpointStateKind.EMPTY
    assert state.is_provider_payload_ready is False
    assert state.message_count == 0


def test_plain_human_ai_conversation_is_provider_ready():
    state = CheckpointStateClassifier.classify([
        HumanMessage(content="hello"),
        AIMessage(content="hi"),
    ])

    assert state.state_kind == CheckpointStateKind.PROVIDER_READY
    assert state.is_provider_payload_ready is True
    assert state.checkpoint_valid is True


def test_final_ai_with_model_next_remains_provider_ready():
    state = classify_checkpoint(
        [
            HumanMessage(content="hello"),
            AIMessage(content="final answer"),
        ],
        next_nodes=["model"],
    )

    assert state.state_kind == CheckpointStateKind.PROVIDER_READY
    assert state.is_provider_payload_ready is True
    assert state.next_nodes == ["model"]
    assert state.checkpoint_valid is True


def test_tool_result_next_model_is_recoverable_checkpoint():
    state = classify_checkpoint(
        [
            HumanMessage(content="list"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_ls", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_ls", name="bash"),
        ],
        next_nodes=["model"],
    )

    assert state.state_kind == CheckpointStateKind.NEXT_MODEL_AFTER_TOOL_RESULT
    assert state.is_provider_payload_ready is True
    assert state.is_recoverable is True
    assert state.closed_tool_call_ids == ["call_ls"]
    assert state.bad_indices == []


def test_open_hitl_tool_call_is_legal_checkpoint_not_provider_ready():
    state = classify_checkpoint(
        [
            HumanMessage(content="write"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_write", "name": "file_write", "args": {}}],
            ),
        ],
        next_nodes=["HumanInTheLoopMiddleware.after_model"],
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_write"]})],
    )

    assert state.state_kind == CheckpointStateKind.HITL_OPEN_TOOL_CALL
    assert state.requires_human_input is True
    assert state.is_provider_payload_ready is False
    assert state.is_recoverable is True
    assert state.open_tool_call_ids == ["call_write"]


def test_orphan_tool_call_without_interrupt_is_invalid():
    state = classify_checkpoint([
        HumanMessage(content="run"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_missing", "name": "bash", "args": {}}],
        ),
    ])

    assert state.state_kind == CheckpointStateKind.INVALID_ORPHAN_TOOL_CALL
    assert state.checkpoint_valid is False
    assert state.orphan_tool_call_ids == ["call_missing"]
    assert state.bad_indices == [1]


def test_orphan_tool_message_without_assistant_call_is_invalid():
    state = classify_checkpoint([
        HumanMessage(content="run"),
        ToolMessage(content="orphan", tool_call_id="call_orphan", name="bash"),
    ])

    assert state.state_kind == CheckpointStateKind.INVALID_ORPHAN_TOOL_MESSAGE
    assert state.checkpoint_valid is False
    assert state.orphan_tool_message_ids == ["call_orphan"]
    assert state.bad_indices == [1]


def test_parallel_tool_calls_all_closed_are_provider_ready():
    state = classify_checkpoint([
        HumanMessage(content="inspect"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call_a", "name": "file_inspect", "args": {}},
                {"id": "call_b", "name": "file_inspect", "args": {}},
            ],
        ),
        ToolMessage(content="a", tool_call_id="call_a", name="file_inspect"),
        ToolMessage(content="b", tool_call_id="call_b", name="file_inspect"),
        AIMessage(content="done"),
    ])

    assert state.state_kind == CheckpointStateKind.PROVIDER_READY
    assert state.closed_tool_call_ids == ["call_a", "call_b"]
    assert state.checkpoint_valid is True


def test_parallel_tool_calls_partially_missing_are_invalid():
    state = classify_checkpoint([
        HumanMessage(content="inspect"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call_a", "name": "file_inspect", "args": {}},
                {"id": "call_b", "name": "file_inspect", "args": {}},
            ],
        ),
        ToolMessage(content="a", tool_call_id="call_a", name="file_inspect"),
    ])

    assert state.state_kind == CheckpointStateKind.INVALID_ORPHAN_TOOL_CALL
    assert state.closed_tool_call_ids == ["call_a"]
    assert state.orphan_tool_call_ids == ["call_b"]
    assert state.bad_indices == [1, 2]


def test_stale_interrupt_with_closed_tool_call_is_not_hitl_open():
    snapshot = SimpleNamespace(
        values={"messages": [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_done", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_done", name="bash"),
        ]},
        next=("HumanInTheLoopMiddleware.after_model",),
        interrupts=[SimpleNamespace(value={"tool_call_ids": ["call_done"]})],
    )

    state = classify_checkpoint_snapshot(snapshot)

    assert state.state_kind == CheckpointStateKind.PROVIDER_READY
    assert state.requires_human_input is False
    assert state.checkpoint_valid is True
