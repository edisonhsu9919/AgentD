"""Phase v0.4.3 — tool-loop circuit breaker and canonical args tests."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge.store import build_frontmatter, ensure_knowledge_dirs, write_knowledge_doc
from tools.base import ToolContext
from tools.knowledge_routing import reset_knowledge_route_state
from tools.registry import (
    ToolLoopCircuitBreaker,
    get_registry,
    get_tool_loop_guard_diagnostics,
    reset_tool_call_counter,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


@pytest.fixture(autouse=True)
def knowledge_root(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge.store.settings.workspace_root", str(tmp_path))
    ensure_knowledge_dirs()
    return str(tmp_path / "knowledge")


@pytest.fixture
def populated_knowledge():
    fm = build_frontmatter(
        title="Loop Guard Notes",
        description="Longer sample for canonical offset/limit tests",
        kind="md",
        owner="user-a",
        permission="public",
        tags=["guard"],
    )
    body = "\n".join(f"line {i}" for i in range(1, 241))
    write_knowledge_doc("guard001", fm, body)


def _make_ctx(session_id: str, run_id: str = "") -> ToolContext:
    return ToolContext(
        user_id="user-a",
        session_id=session_id,
        user_root="/tmp/test-user",
        session_dir="/tmp/test-user/sessions/test-session",
        workspace_dir="/tmp/test-user/sessions/test-session",
        venv_bin="",
        publish=AsyncMock(),
        run_id=run_id,
    )


def _get_lc_tool(name: str, ctx: ToolContext):
    registry = get_registry()
    tools = registry.get_langchain_tools(ctx)
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Missing tool {name}")


class TestKnowledgeCanonicalDedup:
    @pytest.mark.asyncio
    async def test_knowledge_read_default_args_share_same_signature(self, populated_knowledge):
        session_id = "phase-v043-read"
        run_id = "phase-v043-read-run"
        reset_tool_call_counter(session_id)
        reset_knowledge_route_state(run_id)

        ctx = _make_ctx(session_id=session_id, run_id=run_id)
        catalog = _get_lc_tool("knowledge_catalog", ctx)
        reader = _get_lc_tool("knowledge_read", ctx)

        await catalog.ainvoke({})

        for _ in range(3):
            result = await reader.ainvoke({"doc_id": "guard001"})
            data = json.loads(result)
            assert data["doc_id"] == "guard001"
            assert data["offset"] == 1
            assert data["limit"] == 100

        blocked = await reader.ainvoke({"doc_id": "guard001", "offset": 1, "limit": 100})
        assert "BLOCKED" in blocked

    @pytest.mark.asyncio
    async def test_knowledge_read_advanced_offset_is_not_misidentified(self, populated_knowledge):
        session_id = "phase-v043-advance"
        run_id = "phase-v043-advance-run"
        reset_tool_call_counter(session_id)
        reset_knowledge_route_state(run_id)

        ctx = _make_ctx(session_id=session_id, run_id=run_id)
        catalog = _get_lc_tool("knowledge_catalog", ctx)
        reader = _get_lc_tool("knowledge_read", ctx)

        await catalog.ainvoke({})

        first = json.loads(await reader.ainvoke({"doc_id": "guard001"}))
        second = json.loads(await reader.ainvoke({"doc_id": "guard001", "offset": 101, "limit": 100}))

        assert first["offset"] == 1
        assert second["offset"] == 101
        assert second["content"] != first["content"]


class TestToolLoopCircuitBreaker:
    @pytest.mark.asyncio
    async def test_skill_null_loop_hard_stops_on_seventh_call(self):
        session_id = "phase-v043-skill"
        reset_tool_call_counter(session_id)
        ctx = _make_ctx(session_id=session_id)
        skill = _get_lc_tool("skill", ctx)

        for _ in range(3):
            result = await skill.ainvoke({"action": "load", "name": "null"})
            assert "Skill not found" in result

        assert "BLOCKED" in await skill.ainvoke({"action": "load", "name": "\"null\""})
        assert "BLOCKED" in await skill.ainvoke({"action": "load", "name": "null"})
        assert "BLOCKED" in await skill.ainvoke({"action": "load", "name": "None"})

        with pytest.raises(ToolLoopCircuitBreaker) as excinfo:
            await skill.ainvoke({"action": "load", "name": "null"})

        exc = excinfo.value
        assert exc.tool_name == "skill"
        assert exc.canonical_args == {"action": "load"}
        assert exc.identical_call_count == 7
        assert exc.blocked_count >= 4

        diagnostics = get_tool_loop_guard_diagnostics(session_id)
        assert diagnostics["tool_loop_guard_triggered"] is True
        assert diagnostics["tool_loop_guard_tool_name"] == "skill"
        assert diagnostics["tool_loop_guard_identical_call_count"] == 7

    @pytest.mark.asyncio
    async def test_worker_restores_idle_when_hard_breaker_raises(self):
        from agent.worker import AgentWorker

        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())
        breaker = ToolLoopCircuitBreaker(
            session_id=session_id,
            tool_name="skill",
            canonical_args={"action": "load"},
            blocked_count=4,
            identical_call_count=7,
            reason="identical_tool_call_loop",
            message="loop stopped",
        )

        worker = AgentWorker(worker_id="phase-v043")
        worker._execute_start = AsyncMock(side_effect=breaker)
        worker._bridge_child_failure = AsyncMock()
        worker._publish = AsyncMock()

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.worker.scheduler.mark_failed", new=AsyncMock()) as mock_mark_failed,
            patch("agent.executor._update_db_status", new=AsyncMock()) as mock_update_status,
        ):
            await worker._execute_run(run_id, session_id, "start", {})

        mock_mark_failed.assert_awaited_once()
        mock_update_status.assert_awaited_once_with(session_id, "idle")
        status_event = worker._publish.await_args_list[0].args[1]
        error_event = worker._publish.await_args_list[1].args[1]

        assert status_event == {"event": "status_change", "status": "idle"}
        assert error_event["code"] == "tool_loop_circuit_breaker"
        assert error_event["tool_name"] == "skill"
        assert error_event["canonical_args"] == {"action": "load"}
        assert error_event["identical_call_count"] == 7

    @pytest.mark.asyncio
    async def test_worker_restores_idle_for_recoverable_provider_timeout(self):
        from agent.executor import RecoverableProviderTimeout
        from agent.worker import AgentWorker

        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())
        timeout = RecoverableProviderTimeout(
            httpx.ReadTimeout(""),
            {
                "checkpoint_next": ["model"],
                "checkpoint_valid": True,
                "recoverable_model_continuation": True,
            },
        )

        worker = AgentWorker(worker_id="phase-v043")
        worker._execute_resume = AsyncMock(side_effect=timeout)
        worker._bridge_child_failure = AsyncMock()
        worker._publish = AsyncMock()

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.worker.scheduler.mark_failed", new=AsyncMock()) as mock_mark_failed,
            patch("agent.executor._update_db_status", new=AsyncMock()) as mock_update_status,
        ):
            await worker._execute_run(run_id, session_id, "resume", {"decisions": []})

        mock_mark_failed.assert_awaited_once()
        mock_update_status.assert_awaited_once_with(session_id, "idle")
        status_event = worker._publish.await_args_list[0].args[1]
        error_event = worker._publish.await_args_list[1].args[1]

        assert status_event == {"event": "status_change", "status": "idle"}
        assert error_event["code"] == "provider_timeout_retryable"
        assert error_event["message"].startswith("ReadTimeout:")

    @pytest.mark.asyncio
    async def test_worker_dispatches_continue_run_type(self):
        from agent.worker import AgentWorker

        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())
        worker = AgentWorker(worker_id="phase-v043")
        worker._execute_continue = AsyncMock()
        worker._assert_run_returned_terminal_state = AsyncMock()
        worker._bridge_child_result = AsyncMock()

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.worker.scheduler.mark_completed", new=AsyncMock()),
        ):
            await worker._execute_run(
                run_id,
                session_id,
                "continue",
                {"mode": "retry_model_node"},
            )

        worker._execute_continue.assert_awaited_once_with(
            session_id,
            run_id,
            {"mode": "retry_model_node"},
        )


class TestTranscriptIntegrity:
    @pytest.mark.asyncio
    async def test_hitl_checkpoint_next_enters_waiting_not_idle(self):
        from agent.executor import _handle_pending_interrupt_or_unclosed_tools

        session_id = str(uuid.uuid4())
        snapshot = SimpleNamespace(
            values={"messages": [
                HumanMessage(content="write"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_write",
                        "name": "file_write",
                        "args": {"path": "x.txt", "content": "x"},
                    }],
                ),
            ]},
            interrupts=[],
            next=("HumanInTheLoopMiddleware.after_model",),
        )

        agent = AsyncMock()
        publish = AsyncMock()
        with patch("agent.executor._handle_interrupt", new=AsyncMock(return_value=True)) as mock_handle:
            handled = await _handle_pending_interrupt_or_unclosed_tools(
                agent,
                {"configurable": {"thread_id": session_id}},
                session_id,
                "/tmp/session",
                snapshot,
                publish,
            )

        assert handled is True
        synthetic_snapshot = mock_handle.await_args.args[2]
        interrupt_data = synthetic_snapshot.interrupts[0].value
        assert interrupt_data["tool_call_ids"] == ["call_write"]
        assert interrupt_data["action_requests"][0]["name"] == "file_write"

    @pytest.mark.asyncio
    async def test_unclosed_non_hitl_tool_call_refuses_terminal_idle(self):
        from agent.executor import _handle_pending_interrupt_or_unclosed_tools

        snapshot = SimpleNamespace(
            values={"messages": [
                HumanMessage(content="list"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call_list", "name": "list_dir", "args": {}}],
                ),
            ]},
            interrupts=[],
            next=(),
        )

        with (
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()),
            pytest.raises(RuntimeError, match="unclosed non-HITL tool calls"),
        ):
            await _handle_pending_interrupt_or_unclosed_tools(
                AsyncMock(),
                {"configurable": {"thread_id": str(uuid.uuid4())}},
                str(uuid.uuid4()),
                "/tmp/session",
                snapshot,
                AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_runtime_message_persistence_is_idempotent_by_tool_call_id(self):
        from agent.executor import _persist_runtime_message_once

        db = AsyncMock()
        session_id = uuid.uuid4()
        existing_keys: set[str] = set()
        message = AIMessage(
            content="",
            tool_calls=[{"id": "call_once", "name": "skill", "args": {"action": "list"}}],
        )

        with patch("agent.executor.session_svc.create_message", new=AsyncMock()) as mock_create:
            first = await _persist_runtime_message_once(db, session_id, message, existing_keys)
            second = await _persist_runtime_message_once(db, session_id, message, existing_keys)

        assert first is True
        assert second is False
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_result_persistence_is_idempotent_by_tool_call_id(self):
        from agent.executor import _persist_runtime_message_once

        db = AsyncMock()
        session_id = uuid.uuid4()
        existing_keys: set[str] = set()
        message = ToolMessage(content="ok", tool_call_id="call_once")

        with patch("agent.executor.session_svc.create_message", new=AsyncMock()) as mock_create:
            first = await _persist_runtime_message_once(db, session_id, message, existing_keys)
            second = await _persist_runtime_message_once(db, session_id, message, existing_keys)

        assert first is True
        assert second is False
        mock_create.assert_awaited_once()


class TestPermissionResumeIntegrity:
    def test_transcript_integrity_diagnostics_are_exposed(self):
        from agent.executor import _get_transcript_integrity_diagnostics

        agent = SimpleNamespace(_transcript_integrity_error={
            "code": "TRANSCRIPT_INTEGRITY_ERROR",
            "issues": [{"index": 1, "missing_tool_call_ids": ["call_old"]}],
        })

        diagnostics = _get_transcript_integrity_diagnostics(agent)

        assert diagnostics["transcript_integrity_error"] == "TRANSCRIPT_INTEGRITY_ERROR"
        assert diagnostics["transcript_integrity_issues"][0]["index"] == 1

    def test_provider_timeout_after_tool_result_is_retryable(self):
        from agent.executor import _build_exception_diagnostics

        messages = [
            HumanMessage(content="list"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_bash", name="bash"),
        ]
        snapshot = SimpleNamespace(values={"messages": messages}, next=("model",), interrupts=[])

        diagnostics = _build_exception_diagnostics(
            httpx.ReadTimeout(""),
            snapshot,
            messages,
        )

        assert diagnostics["exception_type"] == "ReadTimeout"
        assert diagnostics["checkpoint_next"] == ["model"]
        assert diagnostics["checkpoint_valid"] is True
        assert diagnostics["checkpoint_bad_indices"] == []
        assert diagnostics["recoverable_model_continuation"] is True

    @pytest.mark.asyncio
    async def test_execute_graph_records_checkpoint_on_generic_provider_timeout(self):
        from agent.executor import RecoverableProviderTimeout, _execute_graph

        messages = [
            HumanMessage(content="list"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_bash", name="bash"),
        ]
        snapshot = SimpleNamespace(values={"messages": messages}, next=("model",), interrupts=[])

        class FakeAgent:
            async def aget_state(self, config):
                return snapshot

        with (
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()) as mock_diag,
            patch("agent.executor._stream_and_translate", new=AsyncMock(side_effect=httpx.ReadTimeout(""))),
        ):
            with pytest.raises(RecoverableProviderTimeout):
                await _execute_graph(
                    FakeAgent(),
                    None,
                    {"configurable": {"thread_id": "phase-v043-timeout"}},
                    "phase-v043-timeout",
                    "/tmp/session",
                    AsyncMock(),
                )

        assert mock_diag.await_count >= 2
        assert mock_diag.await_args.args[2] == messages

    def test_runtime_retryability_comes_from_failed_run_diagnostics(self):
        from session.router import _diagnostics_allow_model_retry, _error_looks_like_provider_timeout

        assert _diagnostics_allow_model_retry({
            "recoverable_model_continuation": True,
            "checkpoint_valid": True,
            "checkpoint_next": ["model"],
        })
        assert not _diagnostics_allow_model_retry({
            "recoverable_model_continuation": True,
            "checkpoint_valid": False,
            "checkpoint_next": ["model"],
        })
        assert _error_looks_like_provider_timeout("ReadTimeout: ")
        assert not _error_looks_like_provider_timeout("ValueError: bad")

    @pytest.mark.asyncio
    async def test_runtime_retryability_can_fallback_to_checkpoint(self, tmp_path):
        from session.router import _checkpoint_allows_model_retry

        messages = [
            HumanMessage(content="list"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {}}],
            ),
            ToolMessage(content="ok", tool_call_id="call_bash", name="bash"),
        ]
        snapshot = SimpleNamespace(values={"messages": messages}, next=("model",), interrupts=[])

        class FakeAgent:
            async def aget_state(self, config):
                return snapshot

        session = SimpleNamespace(
            id=uuid.uuid4(),
            agent_id="assistant",
            model_id="test-model",
        )
        user = SimpleNamespace(id=uuid.uuid4(), workspace=str(tmp_path))

        with patch("agent.runtime.build_agent", new=AsyncMock(return_value=FakeAgent())):
            assert await _checkpoint_allows_model_retry(session, user)

    @pytest.mark.asyncio
    async def test_error_prompt_can_recover_resumed_hitl_checkpoint(self, tmp_path):
        from session.router import _inspect_open_hitl_recovery

        messages = [
            HumanMessage(content="run"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_bash", "name": "bash", "args": {"command": "ls"}}],
            ),
        ]
        snapshot = SimpleNamespace(
            values={"messages": messages},
            next=("HumanInTheLoopMiddleware.after_model",),
            interrupts=[SimpleNamespace(value={
                "action_requests": [{"name": "bash", "args": {"command": "ls"}}],
                "tool_call_ids": ["call_bash"],
            })],
        )

        class FakeAgent:
            async def aget_state(self, config):
                return snapshot

        session = SimpleNamespace(
            id=uuid.uuid4(),
            agent_id="assistant",
            model_id="test-model",
        )
        user = SimpleNamespace(id=uuid.uuid4(), workspace=str(tmp_path))
        permission = SimpleNamespace(status="resumed")

        with (
            patch("agent.runtime.build_agent", new=AsyncMock(return_value=FakeAgent())),
            patch(
                "permission.service.get_permission_request_by_tool_call",
                new=AsyncMock(return_value=permission),
            ) as mock_get_permission,
        ):
            recovery = await _inspect_open_hitl_recovery(AsyncMock(), session, user)

        assert recovery.action == "resume"
        assert recovery.decisions == [{"type": "approve"}]
        mock_get_permission.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_prompt_enqueues_resume_instead_of_new_user_message(self):
        from session.router import _OpenHitlRecovery, _recover_open_hitl_before_prompt

        session = SimpleNamespace(id=uuid.uuid4(), status="error")
        user = SimpleNamespace(id=uuid.uuid4(), workspace="/tmp/user")
        db = AsyncMock()
        run = SimpleNamespace(id=uuid.uuid4())

        with (
            patch(
                "session.router._inspect_open_hitl_recovery",
                new=AsyncMock(return_value=_OpenHitlRecovery(
                    action="resume",
                    decisions=[{"type": "approve"}],
                )),
            ),
            patch("agent.scheduler.enqueue_resume", new=AsyncMock(return_value=run)) as mock_enqueue,
            patch("session.router.session_svc.update_session_status", new=AsyncMock()) as mock_status,
        ):
            result = await _recover_open_hitl_before_prompt(db, session, user)

        mock_enqueue.assert_awaited_once_with(db, session.id, [{"type": "approve"}])
        mock_status.assert_awaited_once_with(db, session.id, "queued")
        db.commit.assert_awaited_once()
        assert result["recovered"] is True
        assert result["mode"] == "resume_open_hitl"

    @pytest.mark.asyncio
    async def test_hitl_resume_bypasses_pre_tools_adjacency_gate(self):
        from agent.executor import _execute_graph
        from langgraph.types import Command

        tool_call_message = AIMessage(
            content="",
            tool_calls=[{
                "id": "call_bash",
                "name": "bash",
                "args": {"command": "ls"},
            }],
        )
        snapshot = SimpleNamespace(
            values={"messages": [
                HumanMessage(content="list files"),
                tool_call_message,
            ]},
            interrupts=[SimpleNamespace(value={
                "action_requests": [{
                    "name": "bash",
                    "args": {"command": "ls"},
                }],
                "tool_call_ids": ["call_bash"],
            })],
            next=("HumanInTheLoopMiddleware.after_model",),
        )

        class FakeAgent:
            async def aget_state(self, config):
                return snapshot

        with (
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()),
            patch("agent.executor._ensure_checkpoint_tool_adjacency_ready", new=AsyncMock()) as mock_gate,
            patch("agent.executor._stream_and_translate", new=AsyncMock(return_value=True)) as mock_stream,
            patch("agent.executor._is_subtask_waiting", new=AsyncMock(return_value=False)),
            patch("agent.executor._handle_pending_interrupt_or_unclosed_tools", new=AsyncMock(return_value=True)),
        ):
            await _execute_graph(
                FakeAgent(),
                Command(resume={"decisions": [{"type": "approve"}]}),
                {"configurable": {"thread_id": "phase-v043-hitl-resume"}},
                "phase-v043-hitl-resume",
                "/tmp/session",
                AsyncMock(),
            )

        mock_gate.assert_not_awaited()
        mock_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_graph_rejects_invalid_checkpoint_before_stream(self):
        from agent.executor import _execute_graph

        class FakeAgent:
            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": [
                    HumanMessage(content="run"),
                    AIMessage(
                        content="",
                        tool_calls=[{
                            "id": "call_missing",
                            "name": "bash",
                            "args": {},
                        }],
                    ),
                    HumanMessage(content="next"),
                ]})

        with (
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()),
            patch("agent.executor._load_tool_messages_from_persisted_session", new=AsyncMock(return_value=[])),
            patch("agent.executor._stream_and_translate", new=AsyncMock()) as mock_stream,
        ):
            with pytest.raises(RuntimeError, match="[Cc]heckpoint tool adjacency"):
                await _execute_graph(
                    FakeAgent(),
                    {"messages": [{"role": "user", "content": "hi"}]},
                    {"configurable": {"thread_id": "phase-v043-invalid"}},
                    "phase-v043-invalid",
                    "/tmp/session",
                    AsyncMock(),
                )

        mock_stream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subtask_continuation_gate_repairs_from_persisted_tool_results(self):
        from agent.executor import (
            _checkpoint_tool_adjacency_is_valid,
            _ensure_checkpoint_tool_adjacency_ready,
        )
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        class FakeAgent:
            def __init__(self, messages):
                self.messages = list(messages)

            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": self.messages})

            async def aupdate_state(self, config, values):
                incoming = values.get("messages", [])
                if incoming and isinstance(incoming[0], RemoveMessage) and incoming[0].id == REMOVE_ALL_MESSAGES:
                    self.messages = [msg for msg in incoming[1:] if not isinstance(msg, RemoveMessage)]
                else:
                    self.messages.extend(incoming)
                return {"configurable": {"thread_id": config["configurable"]["thread_id"]}}

        tool_call_message = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_todo", "name": "todo_update", "args": {}},
                {"id": "call_child", "name": "launch_subagent", "args": {}},
            ],
        )
        agent = FakeAgent([
            HumanMessage(content="delegate"),
            tool_call_message,
            AIMessage(
                content="[Sub-task completed]\nsummary",
                additional_kwargs={"agentd_internal": "subtask_result_bridge"},
            ),
            HumanMessage(content="[Subtask Continuation - internal only]\n\nContinue."),
        ])

        with patch(
            "agent.executor._load_tool_messages_from_persisted_session",
            new=AsyncMock(return_value=[
                ToolMessage(content="todo", tool_call_id="call_todo", name="todo_update"),
                ToolMessage(content="waiting", tool_call_id="call_child", name="launch_subagent"),
            ]),
        ):
            await _ensure_checkpoint_tool_adjacency_ready(
                agent,
                {"configurable": {"thread_id": "parent-session"}},
                "parent-session",
                strict=True,
            )

        assert _checkpoint_tool_adjacency_is_valid(agent.messages)
        assert [getattr(msg, "tool_call_id", None) for msg in agent.messages[2:4]] == [
            "call_todo",
            "call_child",
        ]
        assert isinstance(agent.messages[4], AIMessage)
        assert agent.messages[4].additional_kwargs["agentd_internal"] == "subtask_result_bridge"

    @pytest.mark.asyncio
    async def test_wait_parent_run_settled_times_out_fail_closed(self):
        from agent.worker import AgentWorker

        class SessionCtx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        worker = AgentWorker(worker_id="phase-v043")
        with (
            patch("agent.worker.AsyncSessionLocal", return_value=SessionCtx()),
            patch("agent.worker.scheduler.get_active_run", new=AsyncMock(return_value=SimpleNamespace())),
        ):
            settled = await worker._wait_parent_run_settled(uuid.uuid4(), timeout_seconds=0.01)

        assert settled is False

    @pytest.mark.asyncio
    async def test_checkpoint_repair_inserts_missing_tools_before_subtask_bridge(self):
        from agent.executor import (
            _checkpoint_tool_adjacency_is_valid,
            _repair_checkpoint_tool_adjacency,
        )
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        class FakeAgent:
            def __init__(self, messages):
                self.messages = list(messages)

            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": self.messages})

            async def aupdate_state(self, config, values):
                incoming = values.get("messages", [])
                if incoming and isinstance(incoming[0], RemoveMessage) and incoming[0].id == REMOVE_ALL_MESSAGES:
                    self.messages = [msg for msg in incoming[1:] if not isinstance(msg, RemoveMessage)]
                else:
                    self.messages.extend(incoming)
                return {"configurable": {"thread_id": config["configurable"]["thread_id"]}}

        tool_call_message = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_catalog", "name": "knowledge_catalog", "args": {}},
                {"id": "call_search", "name": "knowledge_search", "args": {}},
                {"id": "call_todo", "name": "todo_update", "args": {}},
                {"id": "call_child", "name": "launch_subagent", "args": {}},
            ],
        )
        agent = FakeAgent([
            HumanMessage(content="research"),
            tool_call_message,
            AIMessage(content="[Sub-task completed]\nsummary"),
            HumanMessage(content="[Subtask Continuation - internal only]\n\nContinue."),
        ])
        candidates = [
            ToolMessage(content="catalog", tool_call_id="call_catalog", name="knowledge_catalog"),
            ToolMessage(content="search", tool_call_id="call_search", name="knowledge_search"),
            ToolMessage(content="todo", tool_call_id="call_todo", name="todo_update"),
            ToolMessage(content="waiting", tool_call_id="call_child", name="launch_subagent"),
        ]

        await _repair_checkpoint_tool_adjacency(
            agent,
            {"configurable": {"thread_id": "parent-session"}},
            "parent-session",
            candidate_tool_messages=candidates,
            strict=True,
        )

        assert _checkpoint_tool_adjacency_is_valid(agent.messages)
        assert agent.messages[1] is tool_call_message
        assert [getattr(msg, "tool_call_id", None) for msg in agent.messages[2:6]] == [
            "call_catalog",
            "call_search",
            "call_todo",
            "call_child",
        ]
        assert isinstance(agent.messages[6], AIMessage)
        assert "[Sub-task completed]" in agent.messages[6].content

    @pytest.mark.asyncio
    async def test_stale_interrupt_with_closed_tool_call_does_not_reenter_permission(self):
        from agent.executor import _handle_pending_interrupt_or_unclosed_tools

        session_id = str(uuid.uuid4())
        snapshot = SimpleNamespace(
            values={"messages": [
                HumanMessage(content="write"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_write",
                        "name": "file_write",
                        "args": {"path": "x.txt", "content": "x"},
                    }],
                ),
                ToolMessage(
                    content="Written 1 bytes to x.txt",
                    tool_call_id="call_write",
                    name="file_write",
                ),
                AIMessage(content="done"),
            ]},
            interrupts=[SimpleNamespace(value={
                "action_requests": [{
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
                "tool_call_ids": ["call_write"],
            })],
            next=(),
        )

        with patch("agent.executor._handle_interrupt", new=AsyncMock()) as mock_handle:
            handled = await _handle_pending_interrupt_or_unclosed_tools(
                AsyncMock(),
                {"configurable": {"thread_id": session_id}},
                session_id,
                "/tmp/session",
                snapshot,
                AsyncMock(),
            )

        assert handled is False
        mock_handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_interrupt_ignores_stale_resolved_checkpoint(self):
        from agent.executor import _handle_interrupt

        session_id = str(uuid.uuid4())
        snapshot = SimpleNamespace(
            values={"messages": [
                HumanMessage(content="write"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_write",
                        "name": "file_write",
                        "args": {"path": "x.txt", "content": "x"},
                    }],
                ),
                ToolMessage(
                    content="Written 1 bytes to x.txt",
                    tool_call_id="call_write",
                    name="file_write",
                ),
                AIMessage(content="done"),
            ]},
            interrupts=[SimpleNamespace(value={
                "action_requests": [{
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
                "tool_call_ids": ["call_write"],
            })],
            next=(),
        )

        with (
            patch("permission.policy.load_policy", return_value=SimpleNamespace(mode="ask")),
            patch("agent.executor.perm_svc.get_or_create_permission_request", new=AsyncMock()) as mock_get_or_create,
        ):
            needs_manual = await _handle_interrupt(
                session_id,
                "/tmp/session",
                snapshot,
                {"configurable": {"thread_id": session_id}},
                AsyncMock(),
                AsyncMock(),
            )

        assert needs_manual is False
        mock_get_or_create.assert_not_awaited()

    def test_checkpoint_repair_does_not_pin_execution_config_to_checkpoint_id(self):
        from agent.executor import _merge_updated_config

        config = {"configurable": {"thread_id": "s1"}}
        _merge_updated_config(
            config,
            {"configurable": {
                "thread_id": "s1",
                "checkpoint_ns": "",
                "checkpoint_id": "stale-point-in-time",
            }},
        )

        assert config == {"configurable": {"thread_id": "s1"}}

    @pytest.mark.asyncio
    async def test_permission_resume_does_not_duplicate_auto_approved_requests(self):
        from agent.executor import _handle_interrupt

        session_id = str(uuid.uuid4())
        snapshot = SimpleNamespace(
            values={"messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_write",
                        "name": "file_write",
                        "args": {"path": "x.txt", "content": "x"},
                    }],
                )
            ]},
            interrupts=[SimpleNamespace(value={
                "action_requests": [{
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
                "tool_call_ids": ["call_write"],
            })],
            next=("HumanInTheLoopMiddleware.after_model",),
        )
        agent = AsyncMock()
        agent._agentd_auto_resumed_batches = {("call_write",)}

        with (
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()),
            patch("permission.policy.load_policy", return_value=SimpleNamespace(mode="autopilot")),
            patch("permission.evaluator.evaluate", return_value="allow"),
            patch("agent.executor.perm_svc.get_or_create_permission_request", new=AsyncMock()) as mock_get_or_create,
            pytest.raises(RuntimeError, match="refusing duplicate auto-approve"),
        ):
            await _handle_interrupt(
                session_id,
                "/tmp/session",
                snapshot,
                {"configurable": {"thread_id": session_id}},
                agent,
                AsyncMock(),
            )

        mock_get_or_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manual_approval_reuses_existing_pending_permission(self):
        from agent.executor import _handle_interrupt

        permission_id = uuid.uuid4()
        session_id = str(uuid.uuid4())
        snapshot = SimpleNamespace(
            values={"messages": [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": "call_write",
                        "name": "file_write",
                        "args": {"path": "x.txt", "content": "x"},
                    }],
                )
            ]},
            interrupts=[SimpleNamespace(value={
                "action_requests": [{
                    "name": "file_write",
                    "args": {"path": "x.txt", "content": "x"},
                }],
                "tool_call_ids": ["call_write"],
            })],
            next=("HumanInTheLoopMiddleware.after_model",),
        )

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False
        publish = AsyncMock()

        with (
            patch("permission.policy.load_policy", return_value=SimpleNamespace(mode="ask")),
            patch("agent.executor.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.executor._persist_messages", new=AsyncMock()),
            patch("agent.executor._record_run_diagnostics", new=AsyncMock()),
            patch("agent.executor._update_db_status", new=AsyncMock()),
            patch(
                "agent.executor.perm_svc.get_or_create_permission_request",
                new=AsyncMock(return_value=(
                    SimpleNamespace(id=permission_id, status="pending"),
                    False,
                )),
            ) as mock_get_or_create,
        ):
            needs_manual = await _handle_interrupt(
                session_id,
                "/tmp/session",
                snapshot,
                {"configurable": {"thread_id": session_id}},
                AsyncMock(),
                publish,
            )

        assert needs_manual is True
        mock_get_or_create.assert_awaited_once()
        event = publish.await_args.args[1]
        assert event["event"] == "permission_ask"
        assert event["permission_id"] == str(permission_id)

    @pytest.mark.asyncio
    async def test_worker_refuses_completed_run_when_session_still_running(self):
        from agent.worker import AgentWorker

        class SessionCtx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        worker = AgentWorker(worker_id="phase-v043")

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=SessionCtx()),
            patch(
                "agent.worker.session_svc.get_session",
                new=AsyncMock(return_value=SimpleNamespace(status="running")),
            ),
            pytest.raises(RuntimeError, match="non-terminal session status='running'"),
        ):
            await worker._assert_run_returned_terminal_state(str(uuid.uuid4()))
