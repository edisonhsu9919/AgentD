"""Phase P4-B — Microcompact tests.

Tests cover:
- Candidate identification (compressible tool results)
- Protection logic (frontier turns, protected tools, subtask results)
- Trigger threshold logic
- MicrocompactResult structure
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.microcompact import (
    FRONTIER_TURNS,
    HIGH_COMPRESS_TOOLS,
    MIN_COMPRESSIBLE_COUNT,
    PROTECTED_TOOL_NAMES,
    RATIO_THRESHOLD,
    SINGLE_MSG_SIZE_THRESHOLD,
    MicrocompactResult,
    _find_compressible_candidates,
    _find_protected_indices,
    run_microcompact,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _tool_msg(name: str, content: str, msg_id: str = "t1") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="tc1", id=msg_id)


def _build_messages(tool_count: int = 10, content_size: int = 500) -> list:
    """Build a realistic message list with system + user + AI + tools."""
    msgs = [SystemMessage(content="System prompt", id="sys")]
    for i in range(tool_count):
        msgs.append(HumanMessage(content=f"User message {i}", id=f"h{i}"))
        msgs.append(AIMessage(content=f"Thinking...", id=f"ai{i}",
                              tool_calls=[{"id": f"tc{i}", "name": "grep", "args": {}}]))
        msgs.append(
            ToolMessage(
                content="x" * content_size,
                name="grep",
                tool_call_id=f"tc{i}",
                id=f"tool{i}",
            )
        )
    # Final assistant response
    msgs.append(AIMessage(content="Done!", id="ai_final"))
    return msgs


# ── Candidate identification ─────────────────────────────────────────────


class TestFindCandidates:
    def test_identifies_tool_messages(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="hello", id="h"),
            AIMessage(content="thinking", id="a"),
            _tool_msg("grep", "x" * 500, "t1"),
            _tool_msg("bash", "y" * 1000, "t2"),
        ]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 2
        assert candidates[0]["tool_name"] == "grep"
        assert candidates[1]["tool_name"] == "bash"

    def test_high_compress_tool_enters_even_when_small(self):
        """High-compressibility tools enter candidates regardless of size."""
        msgs = [_tool_msg("grep", "No matches found.", "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 1  # grep is high-compress → always enters
        assert candidates[0]["is_high_compress"] is True

    def test_non_high_compress_skips_small(self):
        """Non-high-compress tools still need chars >= 200."""
        msgs = [_tool_msg("file_write", "ok", "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 0  # file_write is not high-compress, < 200 chars

    def test_skips_non_tool_messages(self):
        msgs = [
            HumanMessage(content="x" * 500, id="h"),
            AIMessage(content="x" * 500, id="a"),
        ]
        candidates = _find_compressible_candidates(msgs)
        assert len(candidates) == 0

    def test_reports_char_count(self):
        msgs = [_tool_msg("bash", "a" * 5000, "t1")]
        candidates = _find_compressible_candidates(msgs)
        assert candidates[0]["chars"] == 5000


# ── Protection logic ─────────────────────────────────────────────────────


class TestFindProtected:
    def test_protects_system_message(self):
        msgs = [SystemMessage(content="sys", id="s"), HumanMessage(content="h", id="h")]
        protected = _find_protected_indices(msgs)
        assert 0 in protected

    def test_protects_frontier_turns(self):
        msgs = _build_messages(tool_count=5)
        protected = _find_protected_indices(msgs)
        # Last FRONTIER_TURNS=2 complete turns should be protected
        # That means the last few messages including the final AI response
        last_idx = len(msgs) - 1
        assert last_idx in protected  # final AI response
        assert last_idx - 1 in protected  # last tool result

    def test_protects_planning_tool(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h", id="h1"),
            AIMessage(content="plan", id="a1"),
            _tool_msg("planning", "task plan content here with enough chars" * 10, "plan1"),
            _tool_msg("grep", "old grep result with enough chars" * 10, "grep1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 3 in protected  # planning tool result

    def test_protects_subtask_result(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            AIMessage(content="[Sub-task completed]\nChild result here", id="a1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 1 in protected

    def test_protects_context_summary(self):
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="[Context Summary]\nPrevious session...", id="h1"),
        ]
        protected = _find_protected_indices(msgs)
        assert 1 in protected


# ── Trigger thresholds ───────────────────────────────────────────────────


class TestTriggerThresholds:
    @pytest.mark.asyncio
    async def test_too_few_messages(self):
        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": [SystemMessage(content="sys", id="s")]}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)

        result = await run_microcompact(mock_agent, {}, "test-session")
        assert result.applied is False
        assert result.reason == "too_few_messages"

    @pytest.mark.asyncio
    async def test_no_snapshot(self):
        mock_agent = MagicMock()
        mock_agent.aget_state = AsyncMock(return_value=None)

        result = await run_microcompact(mock_agent, {}, "test-session")
        assert result.applied is False
        assert result.reason == "no_snapshot"

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """Few small candidates + low ratio → no action."""
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h1", id="h1"),
            AIMessage(content="a1", id="a1"),
            _tool_msg("grep", "x" * 300, "t1"),
            HumanMessage(content="h2", id="h2"),
            AIMessage(content="a2", id="a2"),
        ]
        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.3)
        assert result.applied is False

    @pytest.mark.asyncio
    async def test_triggers_on_high_ratio(self):
        """High context ratio with enough candidates → triggers."""
        msgs = _build_messages(tool_count=10, content_size=500)

        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)
        mock_agent.aupdate_state = AsyncMock()

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.75)
        assert result.applied is True
        assert "ratio" in result.reason

    @pytest.mark.asyncio
    async def test_triggers_on_oversized_message(self):
        """Single oversized message outside frontier → always triggers."""
        msgs = [
            SystemMessage(content="sys", id="s"),
            HumanMessage(content="h1", id="h1"),
            AIMessage(
                content="a1",
                id="a1",
                tool_calls=[{"id": "tc1", "name": "bash", "args": {}}],
            ),
            _tool_msg("bash", "x" * 50_000, "t1"),  # oversized, old turn
            HumanMessage(content="h2", id="h2"),
            AIMessage(
                content="a2",
                id="a2",
                tool_calls=[{"id": "tc2", "name": "grep", "args": {}}],
            ),
            ToolMessage(content="x" * 300, name="grep", tool_call_id="tc2", id="t2"),
            HumanMessage(content="h3", id="h3"),
            AIMessage(
                content="a3",
                id="a3",
                tool_calls=[{"id": "tc3", "name": "grep", "args": {}}],
            ),
            ToolMessage(content="x" * 300, name="grep", tool_call_id="tc3", id="t3"),
            HumanMessage(content="h4", id="h4"),
            AIMessage(content="done", id="a4"),
        ]

        mock_agent = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        mock_agent.aget_state = AsyncMock(return_value=snapshot)
        mock_agent.aupdate_state = AsyncMock()

        result = await run_microcompact(mock_agent, {}, "test-session", context_usage_ratio=0.3)
        assert result.applied is True
        assert "oversized" in result.reason


# ── MicrocompactResult ───────────────────────────────────────────────────


class TestMicrocompactResult:
    def test_structure(self):
        r = MicrocompactResult(applied=True, removed_count=3, replaced_count=1, reason="ratio=0.75")
        assert r.applied is True
        assert r.removed_count == 3
        assert r.replaced_count == 1

    def test_not_applied(self):
        r = MicrocompactResult(applied=False, removed_count=0, replaced_count=0, reason="below_threshold")
        assert r.applied is False


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_ratio_threshold(self):
        assert RATIO_THRESHOLD == 0.6

    def test_min_compressible(self):
        assert MIN_COMPRESSIBLE_COUNT == 8

    def test_frontier_turns(self):
        assert FRONTIER_TURNS == 2

    def test_protected_tools(self):
        assert "planning" in PROTECTED_TOOL_NAMES
        assert "todo_update" in PROTECTED_TOOL_NAMES
        assert "skill" in PROTECTED_TOOL_NAMES

    def test_high_compress_tools(self):
        assert "grep" in HIGH_COMPRESS_TOOLS
        assert "list_dir" in HIGH_COMPRESS_TOOLS
        assert "bash" in HIGH_COMPRESS_TOOLS


class TestMicrocompactToolAdjacency:
    class FakeAgent:
        def __init__(self, messages):
            self.messages = list(messages)
            self.update_payloads = []
            self.update_as_nodes = []

        async def aget_state(self, config):
            snapshot = MagicMock()
            snapshot.values = {"messages": self.messages}
            return snapshot

        async def aupdate_state(self, config, values, as_node=None):
            from langchain_core.messages import RemoveMessage
            from langgraph.graph.message import REMOVE_ALL_MESSAGES

            self.update_payloads.append(values)
            self.update_as_nodes.append(as_node)
            incoming = values.get("messages", [])
            if (
                incoming
                and isinstance(incoming[0], RemoveMessage)
                and incoming[0].id == REMOVE_ALL_MESSAGES
            ):
                self.messages = [
                    msg for msg in incoming[1:]
                    if not isinstance(msg, RemoveMessage)
                ]
            else:
                self.messages.extend(incoming)
            return {"configurable": {"thread_id": config["configurable"]["thread_id"]}}

    def _valid_tool_history(self, tool_count: int = 10, content_size: int = 800):
        messages = [SystemMessage(content="sys", id="sys")]
        for idx in range(tool_count):
            messages.extend([
                HumanMessage(content=f"user {idx}", id=f"user-{idx}"),
                AIMessage(
                    content="",
                    id=f"ai-{idx}",
                    tool_calls=[{
                        "id": f"call-{idx}",
                        "name": "bash",
                        "args": {"command": "printf x"},
                    }],
                ),
                ToolMessage(
                    content="x" * content_size,
                    name="bash",
                    tool_call_id=f"call-{idx}",
                    id=f"tool-{idx}",
                ),
            ])
        return messages

    @pytest.mark.asyncio
    async def test_replaces_tool_result_without_breaking_adjacency(self):
        from agent.microcompact import _find_invalid_tool_adjacency_indices

        agent = self.FakeAgent(self._valid_tool_history())
        result = await run_microcompact(
            agent,
            {"configurable": {"thread_id": "microcompact-test"}},
            "microcompact-test",
            context_usage_ratio=0.75,
        )

        assert result.applied is True
        assert result.removed_count == 0
        assert result.replaced_count > 0
        assert _find_invalid_tool_adjacency_indices(agent.messages) == []
        assert len(agent.messages) == len(self._valid_tool_history())

        compacted_tools = [
            msg for msg in agent.messages
            if isinstance(msg, ToolMessage)
            and "AgentD microcompact" in msg.content
        ]
        assert compacted_tools
        first = compacted_tools[0]
        assert first.id.startswith("tool-")
        assert first.tool_call_id.startswith("call-")
        assert first.name == "bash"

    @pytest.mark.asyncio
    async def test_checkpoint_update_does_not_remove_tool_message_alone(self):
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        agent = self.FakeAgent(self._valid_tool_history())
        await run_microcompact(
            agent,
            {"configurable": {"thread_id": "microcompact-test"}},
            "microcompact-test",
            context_usage_ratio=0.75,
        )

        update_messages = agent.update_payloads[0]["messages"]
        remove_messages = [
            msg for msg in update_messages
            if isinstance(msg, RemoveMessage)
        ]
        assert len(remove_messages) == 1
        assert remove_messages[0].id == REMOVE_ALL_MESSAGES
        assert agent.update_as_nodes == ["__start__"]

    @pytest.mark.asyncio
    async def test_checkpoint_update_requires_as_node_like_langgraph(self):
        class StrictAgent(self.FakeAgent):
            async def aupdate_state(self, config, values, as_node=None):
                if as_node is None:
                    raise ValueError("Ambiguous update, specify as_node")
                return await super().aupdate_state(config, values, as_node=as_node)

        agent = StrictAgent(self._valid_tool_history())
        result = await run_microcompact(
            agent,
            {"configurable": {"thread_id": "microcompact-test"}},
            "microcompact-test",
            context_usage_ratio=0.75,
        )

        assert result.applied is True
        assert result.replaced_count > 0
        assert agent.update_as_nodes == ["__start__"]

    @pytest.mark.asyncio
    async def test_compiled_langgraph_checkpoint_accepts_microcompact_rewrite(self):
        from agent.microcompact import _find_invalid_tool_adjacency_indices
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.graph.message import REMOVE_ALL_MESSAGES
        from langchain_core.messages import RemoveMessage

        def model_node(state):
            return {}

        graph = StateGraph(MessagesState)
        graph.add_node("model", model_node)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        agent = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "microcompact-langgraph"}}
        messages = self._valid_tool_history(tool_count=12, content_size=800)
        await agent.aupdate_state(
            config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]},
            as_node="__start__",
        )

        result = await run_microcompact(
            agent,
            config,
            "microcompact-langgraph",
            context_usage_ratio=0.75,
        )
        snapshot = await agent.aget_state(config)
        updated = snapshot.values["messages"]
        compacted = [
            msg for msg in updated
            if isinstance(msg, ToolMessage)
            and "AgentD microcompact" in str(msg.content)
        ]

        assert result.applied is True
        assert result.removed_count == 0
        assert result.replaced_count == 10
        assert len(updated) == len(messages)
        assert len(compacted) == 10
        assert _find_invalid_tool_adjacency_indices(updated) == []
        assert snapshot.next == ("model",)

    @pytest.mark.asyncio
    async def test_invalid_checkpoint_is_not_rewritten(self):
        messages = [
            SystemMessage(content="sys", id="sys"),
            HumanMessage(content="run", id="u1"),
            AIMessage(
                content="",
                id="ai-1",
                tool_calls=[{
                    "id": "call-1",
                    "name": "bash",
                    "args": {},
                }],
            ),
            HumanMessage(content="next turn", id="u2"),
            AIMessage(content="still enough messages", id="ai-2"),
        ]
        agent = self.FakeAgent(messages)

        result = await run_microcompact(
            agent,
            {"configurable": {"thread_id": "microcompact-test"}},
            "microcompact-test",
            context_usage_ratio=0.9,
        )

        assert result.applied is False
        assert result.reason == "invalid_checkpoint"
        assert agent.update_payloads == []
