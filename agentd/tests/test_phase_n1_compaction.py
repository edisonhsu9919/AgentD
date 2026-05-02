"""Phase N1/N2 — Controlled Compaction & Compaction Continuity tests.

Tests cover:
- Message classification: protected / compactable / frontier
- Protection list: skill full text, planning, todo_update, tool pairs
- Summary generation (mocked LLM) with JSON validation + retry + fallback
- context_summary.json artifact writing
- Checkpoint rewrite via aupdate_state
- should_compact / should_warn threshold logic
- compact_session full orchestration (mocked)
- _validate_summary_json schema checking (7 keys including conversation_highlights)
- Compaction context layer injection in build_system_prompt
- N2-1: task_plan fallback injection after compaction
- N2-2: enriched compaction context (key_decisions, conversation_highlights)
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.compaction import (
    AUTO_TRIGGER_RATIO,
    FRONTIER_KEEP,
    MIN_COMPACTABLE,
    WARNING_RATIO,
    _validate_summary_json,
    classify_messages,
    compact_session,
    generate_summary,
    should_compact,
    should_warn,
    write_context_summary_json,
    _parse_summary_sections,
    _build_summary_input,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sys():
    return SystemMessage(content="You are an assistant.", id="sys-0")


def _human(content="hello", idx=0):
    return HumanMessage(content=content, id=f"human-{idx}")


def _ai(content="response", idx=0, tool_calls=None):
    msg = AIMessage(content=content, id=f"ai-{idx}")
    if tool_calls:
        msg.tool_calls = tool_calls
    return msg


def _tool(content="result", name="bash", call_id="tc-0", idx=0):
    return ToolMessage(content=content, name=name, tool_call_id=call_id, id=f"tool-{idx}")


def _skill_tool(skill_name="pdf-rename", version="1.1.0", idx=0):
    return ToolMessage(
        content=f"[Skill: {skill_name} v{version}]\nFull skill content here...",
        name="skill",
        tool_call_id=f"tc-skill-{idx}",
        id=f"tool-skill-{idx}",
    )


def _planning_tool(content="Plan created", idx=0):
    return ToolMessage(
        content=content, name="planning", tool_call_id=f"tc-plan-{idx}", id=f"tool-plan-{idx}",
    )


def _todo_tool(content="Todo updated", idx=0):
    return ToolMessage(
        content=content, name="todo_update", tool_call_id=f"tc-todo-{idx}", id=f"tool-todo-{idx}",
    )


def _build_long_conversation(n_rounds=20):
    """Build a conversation with system + n_rounds of user/ai pairs + some tool calls."""
    msgs = [_sys()]
    for i in range(n_rounds):
        msgs.append(_human(f"message {i}", idx=i))
        if i == 5:
            # Add a skill load
            msgs.append(_ai("Loading skill", idx=i, tool_calls=[{"id": f"tc-skill-{i}", "name": "skill", "args": {}}]))
            msgs.append(_skill_tool(idx=i))
        elif i == 10:
            # Add planning
            msgs.append(_ai("Creating plan", idx=i, tool_calls=[{"id": f"tc-plan-{i}", "name": "planning", "args": {}}]))
            msgs.append(_planning_tool(idx=i))
        elif i == 15:
            # Add todo
            msgs.append(_ai("Updating todo", idx=i, tool_calls=[{"id": f"tc-todo-{i}", "name": "todo_update", "args": {}}]))
            msgs.append(_todo_tool(idx=i))
        else:
            msgs.append(_ai(f"response {i}", idx=i))
    return msgs


def _valid_summary_json():
    """Return a valid structured JSON summary string."""
    return json.dumps({
        "session_intent": "User wants to rename PDFs using the pdf-rename skill. The project involves splitting structured insurance PDFs and renaming by case number.",
        "key_decisions": [
            "Decision: Use pdf-rename skill. Context: Best fit for structured PDF processing.",
            "Decision: Process files in batch. Context: User has 50+ files, serial would be too slow.",
        ],
        "current_task_state": "Processing step 3 of 5. Just completed text extraction for all PDFs. Currently splitting multi-page documents. Working on scripts/split.py.",
        "active_skill": "pdf-rename v1.1.0 — step 3: splitting",
        "important_artifacts": [
            "/tmp/session/scripts/split.py — PDF splitting logic",
            "/tmp/session/output/ — processed output directory",
        ],
        "conversation_highlights": [
            "User clarified that case numbers follow AZCG-XXXX format",
            "Encountered encoding issue with CJK filenames, resolved by using utf-8 throughout",
        ],
        "next_steps": [
            "Complete remaining 2 files — apply rename logic from split output",
            "Generate summary report of all processed files",
        ],
    })


# ── Test: classify_messages ─────────────────────────────────────────────────


class TestClassifyMessages:

    def test_empty_messages(self):
        msgs, protected, compactable, frontier = classify_messages([])
        assert protected == []
        assert compactable == []
        assert frontier == []

    def test_short_conversation_all_frontier(self):
        """Short conversation (< FRONTIER_KEEP) → everything in frontier."""
        msgs = [_sys(), _human(idx=0), _ai(idx=0), _human(idx=1), _ai(idx=1)]
        _, protected, compactable, frontier = classify_messages(msgs)
        assert compactable == []
        assert len(frontier) == 4  # all non-system messages

    def test_long_conversation_has_compactable(self):
        """Long conversation → middle messages are compactable."""
        msgs = _build_long_conversation(n_rounds=20)
        _, protected, compactable, frontier = classify_messages(msgs)
        assert len(frontier) == FRONTIER_KEEP
        assert len(compactable) > 0
        # Protected should include: last skill, last planning, last todo
        assert len(protected) >= 1

    def test_last_skill_load_protected(self):
        """The LAST skill load ToolMessage should be protected."""
        msgs = [_sys()]
        # Add 30 messages with two skill loads
        for i in range(30):
            msgs.append(_human(idx=i))
            if i == 3:
                msgs.append(_ai(idx=i, tool_calls=[{"id": f"tc-s-{i}", "name": "skill", "args": {}}]))
                msgs.append(_skill_tool("old-skill", "0.1.0", idx=i))
            elif i == 8:
                msgs.append(_ai(idx=i, tool_calls=[{"id": f"tc-s-{i}", "name": "skill", "args": {}}]))
                msgs.append(_skill_tool("new-skill", "1.0.0", idx=i))
            else:
                msgs.append(_ai(idx=i))

        _, protected, compactable, frontier = classify_messages(msgs)

        # The second skill load (new-skill) should be protected, not the first
        protected_msgs = [msgs[i] for i in protected]
        skill_protected = [
            m for m in protected_msgs
            if isinstance(m, ToolMessage) and "[Skill:" in (m.content or "")
        ]
        if skill_protected:
            assert "new-skill" in skill_protected[0].content

    def test_latest_planning_protected(self):
        """The latest planning ToolMessage should be protected if outside frontier."""
        msgs = [_sys()]
        for i in range(30):
            msgs.append(_human(idx=i))
            if i == 5:
                msgs.append(_ai(idx=i, tool_calls=[{"id": f"tc-p-{i}", "name": "planning", "args": {}}]))
                msgs.append(_planning_tool(idx=i))
            else:
                msgs.append(_ai(idx=i))

        _, protected, compactable, frontier = classify_messages(msgs)
        protected_msgs = [msgs[i] for i in protected]
        planning_protected = [
            m for m in protected_msgs
            if isinstance(m, ToolMessage) and getattr(m, "name", "") == "planning"
        ]
        # If planning is outside frontier, it should be protected
        # (depending on conversation length)
        # The planning at i=5 is early, so should be outside frontier
        assert len(planning_protected) >= 1 or any(
            isinstance(msgs[i], ToolMessage) and getattr(msgs[i], "name", "") == "planning"
            for i in frontier
        )

    def test_tool_pairs_not_split(self):
        """AIMessage with tool_calls and its ToolMessage result should not be separated."""
        msgs = [_sys()]
        for i in range(30):
            msgs.append(_human(idx=i))
            msgs.append(_ai(idx=i))

        _, protected, compactable, frontier = classify_messages(msgs)
        # Check that no frontier starts with a ToolMessage orphaned from its AI
        if frontier:
            first_frontier_msg = msgs[frontier[0]]
            if isinstance(first_frontier_msg, ToolMessage):
                # The AI before it should be in protected
                ai_found = any(isinstance(msgs[p], AIMessage) for p in protected)
                assert ai_found

    def test_system_message_excluded(self):
        """SystemMessage at index 0 should not appear in any category."""
        msgs = _build_long_conversation(n_rounds=20)
        _, protected, compactable, frontier = classify_messages(msgs)
        assert 0 not in protected
        assert 0 not in compactable
        assert 0 not in frontier


# ── Test: should_compact / should_warn ──────────────────────────────────────


class TestThresholds:

    def test_should_compact_above_threshold(self):
        assert should_compact(0.90) is True

    def test_should_compact_at_threshold(self):
        assert should_compact(AUTO_TRIGGER_RATIO) is True

    def test_should_compact_below_threshold(self):
        assert should_compact(0.80) is False

    def test_should_compact_none(self):
        assert should_compact(None) is False

    def test_should_warn_above_threshold(self):
        assert should_warn(0.75) is True

    def test_should_warn_at_threshold(self):
        assert should_warn(WARNING_RATIO) is True

    def test_should_warn_below_threshold(self):
        assert should_warn(0.60) is False

    def test_should_warn_none(self):
        assert should_warn(None) is False


# ── Test: _build_summary_input ──────────────────────────────────────────────


class TestBuildSummaryInput:

    def test_formats_messages_correctly(self):
        msgs = [
            _sys(),
            _human("What's the weather?", idx=0),
            _ai("It's sunny.", idx=0),
            _tool("temperature: 25C", name="bash", idx=0),
        ]
        text = _build_summary_input(msgs, [1, 2, 3])
        assert "[User]: What's the weather?" in text
        assert "[Assistant]: It's sunny." in text
        assert "[Tool:bash]: temperature: 25C" in text

    def test_empty_compactable(self):
        msgs = [_sys(), _human(idx=0)]
        text = _build_summary_input(msgs, [])
        assert text == ""


# ── Test: _validate_summary_json ────────────────────────────────────────────


class TestValidateSummaryJson:

    def test_valid_json(self):
        result = _validate_summary_json(_valid_summary_json())
        assert result is not None
        assert "rename PDFs" in result["session_intent"]
        assert isinstance(result["key_decisions"], list)
        assert isinstance(result["conversation_highlights"], list)

    def test_valid_json_with_code_fence(self):
        """Model wraps output in ```json ... ``` — should still parse."""
        text = "```json\n" + _valid_summary_json() + "\n```"
        result = _validate_summary_json(text)
        assert result is not None
        assert "rename PDFs" in result["session_intent"]

    def test_missing_key_returns_none(self):
        incomplete = json.dumps({"session_intent": "test", "key_decisions": []})
        result = _validate_summary_json(incomplete)
        assert result is None

    def test_wrong_type_returns_none(self):
        bad = json.dumps({
            "session_intent": 123,  # should be string
            "key_decisions": [],
            "current_task_state": "test",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
        })
        result = _validate_summary_json(bad)
        assert result is None

    def test_plain_text_returns_none(self):
        result = _validate_summary_json("Acknowledged. Ready for Round 6.")
        assert result is None

    def test_array_returns_none(self):
        result = _validate_summary_json("[1, 2, 3]")
        assert result is None

    def test_active_skill_null_accepted(self):
        data = {
            "session_intent": "test",
            "key_decisions": [],
            "current_task_state": "idle",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
        }
        result = _validate_summary_json(json.dumps(data))
        assert result is not None
        assert result["active_skill"] is None

    def test_active_skill_string_accepted(self):
        data = {
            "session_intent": "test",
            "key_decisions": [],
            "current_task_state": "idle",
            "active_skill": "pdf-rename v1.1.0",
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
        }
        result = _validate_summary_json(json.dumps(data))
        assert result is not None
        assert result["active_skill"] == "pdf-rename v1.1.0"


# ── Test: _parse_summary_sections (JSON + legacy Markdown) ──────────────────


class TestParseSummarySections:

    def test_parses_json_format(self):
        """New JSON format should be parsed correctly."""
        sections = _parse_summary_sections(_valid_summary_json())
        assert "rename PDFs" in sections["session_intent"]
        assert isinstance(sections["key_decisions"], list)
        assert "pdf-rename" in sections["key_decisions"][0]
        assert "splitting" in sections["active_skill"]
        assert isinstance(sections["conversation_highlights"], list)
        assert len(sections["conversation_highlights"]) == 2

    def test_parses_legacy_markdown(self):
        """Legacy Markdown heading format should still work."""
        text = """## SESSION INTENT
User wants to rename PDFs.

## KEY DECISIONS
- Use pdf-rename skill
- Process files in batch

## CURRENT TASK STATE
Processing step 3 of 5.

## ACTIVE SKILL
pdf-rename v1.1.0 — step 3

## IMPORTANT ARTIFACTS
- /tmp/session/scripts/split.py
- /tmp/session/output/

## NEXT STEPS
- Complete remaining 2 files
- Generate report
"""
        sections = _parse_summary_sections(text)
        assert "rename PDFs" in sections["session_intent"]
        assert "pdf-rename" in str(sections["key_decisions"])
        assert "step 3" in sections["current_task_state"]

    def test_handles_missing_sections_markdown(self):
        text = "## SESSION INTENT\nJust chatting."
        sections = _parse_summary_sections(text)
        assert "chatting" in sections["session_intent"]

    def test_unstructured_flag_preserved(self):
        data = {
            "session_intent": "Fallback text",
            "key_decisions": [],
            "current_task_state": "unknown",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
            "_unstructured": True,
        }
        sections = _parse_summary_sections(json.dumps(data))
        assert sections.get("_unstructured") is True


# ── Test: write_context_summary_json ────────────────────────────────────────


class TestWriteContextSummaryJson:

    def test_writes_valid_json_from_structured(self, tmp_path):
        session_dir = str(tmp_path / "session")
        os.makedirs(session_dir, exist_ok=True)

        path = write_context_summary_json(session_dir, _valid_summary_json(), 42)
        assert os.path.isfile(path)

        with open(path, "r") as f:
            data = json.load(f)

        assert data["version"] == 2
        assert data["structured"] is True
        assert data["compacted_through_seq"] == 42
        assert "rename PDFs" in data["session_intent"]
        assert data["compacted_at"] is not None
        assert isinstance(data["key_decisions"], list)

    def test_writes_unstructured_flag(self, tmp_path):
        session_dir = str(tmp_path / "session")
        os.makedirs(session_dir, exist_ok=True)

        fallback = json.dumps({
            "session_intent": "Fallback text",
            "key_decisions": [],
            "current_task_state": "unknown",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
            "_unstructured": True,
        })
        path = write_context_summary_json(session_dir, fallback, 10)

        with open(path, "r") as f:
            data = json.load(f)

        assert data["structured"] is False

    def test_creates_agentd_dir(self, tmp_path):
        session_dir = str(tmp_path / "new_session")
        os.makedirs(session_dir, exist_ok=True)

        write_context_summary_json(session_dir, _valid_summary_json(), 0)
        assert os.path.isdir(os.path.join(session_dir, ".agentd"))

    def test_increments_compaction_count(self, tmp_path):
        session_dir = str(tmp_path / "session")
        os.makedirs(session_dir, exist_ok=True)

        # First compaction
        write_context_summary_json(session_dir, _valid_summary_json(), 10)
        with open(os.path.join(session_dir, ".agentd", "context_summary.json")) as f:
            assert json.load(f)["compaction_count"] == 1

        # Second compaction
        write_context_summary_json(session_dir, _valid_summary_json(), 20)
        with open(os.path.join(session_dir, ".agentd", "context_summary.json")) as f:
            assert json.load(f)["compaction_count"] == 2


# ── Test: generate_summary with validation + retry + fallback ───────────────


class TestGenerateSummary:

    @pytest.mark.asyncio
    async def test_first_attempt_success(self):
        """Valid Markdown on first attempt → return directly."""
        valid_md = (
            "# Session Title\nPDF Rename\n\n"
            "# Current State\nProcessing files\n\n"
            "# Task Specification\nHelp user rename PDFs based on content\n\n"
            "# Files and Artifacts\n- report.pdf\n\n"
            "# Workflow Patterns\n(none)\n\n"
            "# Errors & Corrections\n(none)\n\n"
            "# Active Skill / Plan\npdf-rename v1.2.0\n\n"
            "# Subtasks\n(none)\n\n"
            "# Key Results\n- Identified 3 PDF files\n\n"
            "# Next Steps\n- Process remaining files\n\n"
            "# Worklog\n- Started PDF analysis\n"
        )

        mock_result = MagicMock()
        mock_result.content = valid_md

        with patch("agent.compaction.AsyncSessionLocal") as mock_db_ctx, \
             patch("agent.compaction.ChatOpenAI") as mock_llm_cls:
            mock_db = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("model_config.service.resolve_active_model_config", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = MagicMock(base_url="http://test", api_key="key")

                mock_llm = AsyncMock()
                mock_llm.ainvoke.return_value = mock_result
                mock_llm_cls.return_value = mock_llm

                msgs = [_sys(), _human(idx=0), _ai(idx=0)]
                result = await generate_summary(msgs, [1, 2], "test-model")

                assert "# Current State" in result
                assert "rename PDFs" in result
                assert mock_llm.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_invalid_first_attempt(self):
        """Invalid first attempt → retry → succeed with Markdown."""
        valid_md = (
            "# Session Title\nTest\n\n"
            "# Current State\nDone\n\n"
            "# Task Specification\nHelp user rename PDFs\n\n"
            "# Next Steps\n- Continue\n"
        )

        bad_result = MagicMock()
        bad_result.content = "Acknowledged. Ready for next round."
        good_result = MagicMock()
        good_result.content = valid_md

        with patch("agent.compaction.AsyncSessionLocal") as mock_db_ctx, \
             patch("agent.compaction.ChatOpenAI") as mock_llm_cls:
            mock_db = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("model_config.service.resolve_active_model_config", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = MagicMock(base_url="http://test", api_key="key")

                mock_llm = AsyncMock()
                mock_llm.ainvoke.side_effect = [bad_result, good_result]
                mock_llm_cls.return_value = mock_llm

                msgs = [_sys(), _human(idx=0), _ai(idx=0)]
                result = await generate_summary(msgs, [1, 2], "test-model")

                assert "# Current State" in result
                assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_double_failure(self):
        """Both attempts invalid → fallback to raw text."""
        bad_result = MagicMock()
        bad_result.content = "I don't understand the format."

        with patch("agent.compaction.AsyncSessionLocal") as mock_db_ctx, \
             patch("agent.compaction.ChatOpenAI") as mock_llm_cls:
            mock_db = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("model_config.service.resolve_active_model_config", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = MagicMock(base_url="http://test", api_key="key")

                mock_llm = AsyncMock()
                mock_llm.ainvoke.return_value = bad_result
                mock_llm_cls.return_value = mock_llm

                msgs = [_sys(), _human(idx=0), _ai(idx=0)]
                result = await generate_summary(msgs, [1, 2], "test-model")

                # Fallback returns raw text
                assert "I don't understand" in result
                assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_input_returns_default(self):
        """Empty compactable messages → return default Markdown."""
        msgs = [_sys()]
        result = await generate_summary(msgs, [], "test-model")
        assert "# Session Title" in result
        assert "No significant content" in result


# ── Test: compact_session orchestration (mocked) ───────────────────────────


class TestCompactSessionOrchestration:

    @pytest.mark.asyncio
    async def test_not_enough_messages_skips(self):
        """If total messages < FRONTIER_KEEP + MIN_COMPACTABLE, skip."""
        import uuid as _uuid
        test_sid = str(_uuid.uuid4())

        agent = AsyncMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": [_sys(), _human(idx=0), _ai(idx=0)]}
        agent.aget_state.return_value = snapshot

        result = await compact_session(
            agent=agent,
            config={"configurable": {"thread_id": test_sid}},
            session_id=test_sid,
            session_dir="/tmp/test",
            model_id="test-model",
        )
        assert result["compacted"] is False
        assert result["reason"] == "not_enough_messages"

    @pytest.mark.asyncio
    async def test_too_few_compactable_skips(self):
        """If compactable < MIN_COMPACTABLE, skip."""
        import uuid as _uuid
        test_sid = str(_uuid.uuid4())

        # Build a conversation where most messages are in the frontier
        msgs = [_sys()]
        for i in range(FRONTIER_KEEP + 2):  # Just barely enough total
            msgs.append(_human(idx=i))
            msgs.append(_ai(idx=i))

        agent = AsyncMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        agent.aget_state.return_value = snapshot

        result = await compact_session(
            agent=agent,
            config={"configurable": {"thread_id": test_sid}},
            session_id=test_sid,
            session_dir="/tmp/test",
            model_id="test-model",
        )
        assert result["compacted"] is False

    @pytest.mark.asyncio
    async def test_open_checkpoint_tool_group_defers_summary(self):
        """Hard compact must not insert a summary into an open tool group."""
        import uuid as _uuid
        test_sid = str(_uuid.uuid4())

        msgs = _build_long_conversation(n_rounds=25)
        msgs.append(_ai(
            "parallel tools",
            idx=98,
            tool_calls=[
                {"id": "call_a", "name": "list_dir", "args": {}},
                {"id": "call_b", "name": "launch_subagent", "args": {}},
            ],
        ))
        msgs.append(_tool("files", name="list_dir", call_id="call_a", idx=98))

        agent = AsyncMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        agent.aget_state.return_value = snapshot

        with patch("agent.compaction.generate_summary", new_callable=AsyncMock) as mock_gen:
            result = await compact_session(
                agent=agent,
                config={"configurable": {"thread_id": test_sid}},
                session_id=test_sid,
                session_dir="/tmp/test",
                model_id="test-model",
            )

        assert result["compacted"] is False
        assert result["reason"] == "open_tool_group_defer_summary"
        assert result["source"] == "checkpoint"
        assert result["open_tool_call_ids"] == ["call_b"]
        mock_gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_open_db_tool_group_defers_summary(self):
        """Hard compact also checks DB projection before summary generation."""
        import uuid as _uuid
        test_sid = str(_uuid.uuid4())

        msgs = _build_long_conversation(n_rounds=25)
        agent = AsyncMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        agent.aget_state.return_value = snapshot

        with (
            patch("agent.compaction._db_open_tool_group", new=AsyncMock(return_value=["call_b"])),
            patch("agent.compaction.generate_summary", new_callable=AsyncMock) as mock_gen,
        ):
            result = await compact_session(
                agent=agent,
                config={"configurable": {"thread_id": test_sid}},
                session_id=test_sid,
                session_dir="/tmp/test",
                model_id="test-model",
            )

        assert result["compacted"] is False
        assert result["reason"] == "open_tool_group_defer_summary"
        assert result["source"] == "db"
        assert result["open_tool_call_ids"] == ["call_b"]
        mock_gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_compaction_flow(self, tmp_path):
        """Full happy path: classify → summarize → DB → JSON → checkpoint."""
        import uuid as _uuid
        test_sid = str(_uuid.uuid4())

        # Build a long conversation
        msgs = _build_long_conversation(n_rounds=25)

        agent = AsyncMock()
        snapshot = MagicMock()
        snapshot.values = {"messages": msgs}
        agent.aget_state.return_value = snapshot

        # Mock aupdate_state to return a shorter message list
        new_snapshot = MagicMock()
        new_snapshot.values = {"messages": msgs[-FRONTIER_KEEP:]}
        agent.aupdate_state.return_value = None
        # After rewrite, aget_state returns the shorter list
        agent.aget_state.side_effect = [snapshot, new_snapshot]

        session_dir = str(tmp_path / "session")
        os.makedirs(os.path.join(session_dir, ".agentd"), exist_ok=True)

        publish = AsyncMock()

        with patch("agent.compaction.generate_summary", new_callable=AsyncMock) as mock_gen, \
             patch("agent.compaction.AsyncSessionLocal") as mock_db_ctx:
            mock_gen.return_value = _valid_summary_json()

            # Mock DB context manager
            mock_db = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agent.compaction.session_svc") as mock_svc:
                mock_svc.get_last_message_seq = AsyncMock(return_value=50)
                mock_svc.create_message = AsyncMock()

                result = await compact_session(
                    agent=agent,
                    config={"configurable": {"thread_id": test_sid}},
                    session_id=test_sid,
                    session_dir=session_dir,
                    model_id="test-model",
                    publish=publish,
                )

        assert result["compacted"] is True
        assert result["original_count"] == len(msgs)
        assert result["compacted_count"] > 0
        assert result["frontier_count"] == FRONTIER_KEEP

        # Verify context_summary.json was written
        json_path = os.path.join(session_dir, ".agentd", "context_summary.json")
        assert os.path.isfile(json_path)

        with open(json_path) as f:
            cs = json.load(f)
        assert cs["version"] == 2
        assert cs["structured"] is True

        # Verify compaction_done SSE was published
        published_events = [call[0][1]["event"] for call in publish.call_args_list]
        assert "compaction_done" in published_events


# ── Test: Compaction context layer in build_system_prompt ───────────────────


class TestCompactionContextLayer:

    def test_no_context_without_summary(self, tmp_path):
        """No context_summary.json → compaction_context layer is empty."""
        from agent.runtime import _load_compaction_context_layer
        result = _load_compaction_context_layer(str(tmp_path))
        assert result == ""

    def test_injects_structured_summary(self, tmp_path):
        """Structured context_summary.json → layer contains key fields."""
        from agent.runtime import _load_compaction_context_layer

        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": True,
            "session_intent": "Rename PDF files",
            "current_task_state": "Step 3 of 5",
            "active_skill": "pdf-rename v1.1.0",
            "important_artifacts": ["/tmp/scripts/split.py"],
            "next_steps": ["Complete remaining files"],
            "compaction_count": 2,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert "Prior Context" in result
        assert "Rename PDF files" in result
        assert "Step 3 of 5" in result
        assert "pdf-rename v1.1.0" in result
        assert "split.py" in result
        assert "Complete remaining files" in result
        assert "compacted 2 time(s)" in result

    def test_skips_unstructured_summary(self, tmp_path):
        """Unstructured summary → layer is empty (not injected)."""
        from agent.runtime import _load_compaction_context_layer

        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": False,
            "session_intent": "Fallback text",
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert result == ""

    def test_build_system_prompt_includes_compaction_layer(self, tmp_path):
        """build_system_prompt includes compaction context when available."""
        from agent.runtime import build_system_prompt

        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": True,
            "session_intent": "Test compaction injection",
            "current_task_state": "Running tests",
            "active_skill": None,
            "important_artifacts": [],
            "next_steps": [],
            "compaction_count": 1,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        prompt, diag = build_system_prompt(
            agent_id="build",
            session_dir=str(tmp_path),
        )
        assert "Test compaction injection" in prompt
        assert diag["compaction_context_injected"] is True
        assert diag["system_prompt_layers"]["compaction_context"] > 0


# ── Test: SummarizationMiddleware removed ───────────────────────────────────


class TestMiddlewareRemoved:

    def test_no_summarization_middleware_import(self):
        """Verify SummarizationMiddleware is no longer imported in runtime.py."""
        import importlib
        source = importlib.util.find_spec("agent.runtime").origin
        with open(source, "r") as f:
            lines = f.readlines()
        # Check no active import statement (comments are OK)
        import_lines = [l for l in lines if l.strip().startswith(("from ", "import "))]
        for line in import_lines:
            assert "SummarizationMiddleware" not in line


# ── Test: N2-1 — _has_compaction_occurred + task_plan fallback ──────────────


class TestHasCompactionOccurred:

    def test_no_file_returns_false(self, tmp_path):
        from agent.runtime import _has_compaction_occurred
        assert _has_compaction_occurred(str(tmp_path)) is False

    def test_zero_count_returns_false(self, tmp_path):
        from agent.runtime import _has_compaction_occurred
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        (agentd_dir / "context_summary.json").write_text(
            json.dumps({"compaction_count": 0})
        )
        assert _has_compaction_occurred(str(tmp_path)) is False

    def test_positive_count_returns_true(self, tmp_path):
        from agent.runtime import _has_compaction_occurred
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        (agentd_dir / "context_summary.json").write_text(
            json.dumps({"compaction_count": 2})
        )
        assert _has_compaction_occurred(str(tmp_path)) is True

    def test_corrupt_file_returns_false(self, tmp_path):
        from agent.runtime import _has_compaction_occurred
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        (agentd_dir / "context_summary.json").write_text("not json!!!")
        assert _has_compaction_occurred(str(tmp_path)) is False


class TestTaskPlanFallbackInjection:

    def test_no_plan_no_compaction(self, tmp_path):
        """Fresh session: no plan, no compaction → task_plan not injected."""
        from agent.runtime import build_system_prompt
        _, diag = build_system_prompt(agent_id="build", session_dir=str(tmp_path))
        assert diag["task_plan_injected"] is False
        assert diag["task_plan_chars"] == 0

    def test_plan_exists_but_no_compaction(self, tmp_path):
        """Plan file exists but no compaction → task_plan NOT injected."""
        from agent.runtime import build_system_prompt
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        plan = {
            "active": True,
            "task": {"title": "Test Plan", "summary": "Do something"},
            "steps": [{"title": "Step 1", "status": "in_progress", "detail": "Working on it"}],
        }
        (agentd_dir / "task_plan.json").write_text(json.dumps(plan))

        _, diag = build_system_prompt(agent_id="build", session_dir=str(tmp_path))
        assert diag["task_plan_injected"] is False

    def test_plan_injected_after_compaction(self, tmp_path):
        """After compaction with an active plan → task_plan IS injected."""
        from agent.runtime import build_system_prompt
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()

        # Active plan
        plan = {
            "active": True,
            "task": {"title": "Build feature X", "summary": "Implement the new feature"},
            "steps": [
                {"title": "Step 1", "status": "completed"},
                {"title": "Step 2", "status": "in_progress", "detail": "Working on runtime.py"},
                {"title": "Step 3", "status": "pending"},
            ],
        }
        (agentd_dir / "task_plan.json").write_text(json.dumps(plan))

        # Compaction occurred
        summary = {
            "version": 2,
            "structured": True,
            "compaction_count": 1,
            "session_intent": "Building feature X",
            "current_task_state": "Step 2 in progress",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(summary))

        prompt, diag = build_system_prompt(agent_id="build", session_dir=str(tmp_path))
        assert diag["task_plan_injected"] is True
        assert diag["task_plan_chars"] > 0
        assert "Build feature X" in prompt
        assert "Step 2" in prompt
        assert "Working on runtime.py" in prompt

    def test_inactive_plan_not_injected_even_after_compaction(self, tmp_path):
        """Inactive plan → not injected even after compaction."""
        from agent.runtime import build_system_prompt
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()

        plan = {
            "active": False,
            "task": {"title": "Old Plan"},
            "steps": [],
        }
        (agentd_dir / "task_plan.json").write_text(json.dumps(plan))

        summary = {"version": 2, "structured": True, "compaction_count": 1}
        (agentd_dir / "context_summary.json").write_text(json.dumps(summary))

        _, diag = build_system_prompt(agent_id="build", session_dir=str(tmp_path))
        assert diag["task_plan_injected"] is False

    def test_prompt_assembly_order_reflects_injection(self, tmp_path):
        """Assembly order entry for task_plan should reflect actual injection state."""
        from agent.runtime import build_system_prompt
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()

        plan = {
            "active": True,
            "task": {"title": "Test"},
            "steps": [{"title": "Step 1", "status": "pending"}],
        }
        (agentd_dir / "task_plan.json").write_text(json.dumps(plan))
        summary = {"version": 2, "structured": True, "compaction_count": 1}
        (agentd_dir / "context_summary.json").write_text(json.dumps(summary))

        _, diag = build_system_prompt(agent_id="build", session_dir=str(tmp_path))
        order = diag["prompt_assembly_order"]
        plan_entry = [e for e in order if e["name"] == "task_plan"][0]
        assert plan_entry["injected"] is True
        assert plan_entry["chars"] > 0


# ── Test: N2-2 — Enriched compaction context layer ─────────────────────────


class TestEnrichedCompactionContext:

    def test_key_decisions_in_context(self, tmp_path):
        """Key decisions should appear in compaction context layer."""
        from agent.runtime import _load_compaction_context_layer
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": True,
            "session_intent": "Test",
            "key_decisions": [
                "Decision: Use JSON format. Context: More reliable for local LLMs.",
                "Decision: Keep frontier at 20. Context: Balance between context and compaction.",
            ],
            "current_task_state": "Testing",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
            "compaction_count": 1,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert "Key Decisions" in result
        assert "Use JSON format" in result
        assert "Keep frontier at 20" in result

    def test_conversation_highlights_in_context(self, tmp_path):
        """Conversation highlights should appear in compaction context layer."""
        from agent.runtime import _load_compaction_context_layer
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": True,
            "session_intent": "Test",
            "key_decisions": [],
            "current_task_state": "Testing",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [
                "User prefers batch processing over interactive mode",
                "CJK encoding issue resolved with utf-8",
            ],
            "next_steps": [],
            "compaction_count": 1,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert "Conversation Highlights" in result
        assert "batch processing" in result
        assert "CJK encoding" in result

    def test_empty_highlights_not_rendered(self, tmp_path):
        """Empty conversation_highlights should not produce a section."""
        from agent.runtime import _load_compaction_context_layer
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 2,
            "structured": True,
            "session_intent": "Test intent",
            "key_decisions": [],
            "current_task_state": "idle",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": [],
            "next_steps": [],
            "compaction_count": 1,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert "Conversation Highlights" not in result
        assert "Key Decisions" not in result

    def test_v1_summary_without_highlights_still_works(self, tmp_path):
        """Old v1-style summary without conversation_highlights should not crash."""
        from agent.runtime import _load_compaction_context_layer
        agentd_dir = tmp_path / ".agentd"
        agentd_dir.mkdir()
        data = {
            "version": 1,
            "structured": True,
            "session_intent": "Old format test",
            "current_task_state": "Running",
            "active_skill": None,
            "important_artifacts": [],
            "next_steps": ["Finish test"],
            "compaction_count": 1,
        }
        (agentd_dir / "context_summary.json").write_text(json.dumps(data))

        result = _load_compaction_context_layer(str(tmp_path))
        assert "Old format test" in result
        assert "Finish test" in result
        # No crash, no highlights section
        assert "Conversation Highlights" not in result


# ── Test: Enriched _validate_summary_json with 7 keys ──────────────────────


class TestValidateSummaryJsonV2:

    def test_missing_conversation_highlights_returns_none(self):
        """Missing conversation_highlights → validation fails."""
        incomplete = json.dumps({
            "session_intent": "test",
            "key_decisions": [],
            "current_task_state": "test",
            "active_skill": None,
            "important_artifacts": [],
            # conversation_highlights missing
            "next_steps": [],
        })
        result = _validate_summary_json(incomplete)
        assert result is None

    def test_conversation_highlights_wrong_type_returns_none(self):
        """conversation_highlights as string instead of list → fails."""
        bad = json.dumps({
            "session_intent": "test",
            "key_decisions": [],
            "current_task_state": "test",
            "active_skill": None,
            "important_artifacts": [],
            "conversation_highlights": "should be a list",
            "next_steps": [],
        })
        result = _validate_summary_json(bad)
        assert result is None

    def test_all_seven_keys_valid(self):
        """All 7 keys with correct types → passes."""
        data = {
            "session_intent": "Full test",
            "key_decisions": ["decision 1"],
            "current_task_state": "running",
            "active_skill": "test-skill v1.0.0",
            "important_artifacts": ["file.py"],
            "conversation_highlights": ["highlight 1"],
            "next_steps": ["next 1"],
        }
        result = _validate_summary_json(json.dumps(data))
        assert result is not None
        assert len(result) == 7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
