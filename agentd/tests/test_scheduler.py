"""Tests for Phase C — scheduler, run model, and worker coordination.

Covers:
- AgentRun model schema
- Scheduler enqueue operations (start, resume, abort)
- Claim with FOR UPDATE SKIP LOCKED semantics
- Status transitions (running, completed, failed, cancelled)
- Lease management (renew, expire reclaim)
- Abort signal detection
- Worker publish fallback logic
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.run_models import AgentRun


# ── Unit tests: AgentRun model ────────────────────────────────────────────


class TestAgentRunModel:
    def test_default_values(self):
        """SQLAlchemy defaults apply at DB flush; test explicit construction."""
        run = AgentRun(
            session_id=uuid.uuid4(),
            run_type="start",
            status="queued",
            payload={"user_message": "hello"},
        )
        assert run.status == "queued"
        assert run.worker_id is None
        assert run.lease_expires_at is None
        assert run.error is None

    def test_start_run_type(self):
        run = AgentRun(
            session_id=uuid.uuid4(),
            run_type="start",
            payload={"user_message": "hi", "agent_id": "build"},
        )
        assert run.run_type == "start"
        assert run.payload["user_message"] == "hi"

    def test_resume_run_type(self):
        run = AgentRun(
            session_id=uuid.uuid4(),
            run_type="resume",
            payload={"decisions": [{"type": "approve"}]},
        )
        assert run.run_type == "resume"
        assert len(run.payload["decisions"]) == 1

    def test_abort_run_type(self):
        run = AgentRun(
            session_id=uuid.uuid4(),
            run_type="abort",
            payload={},
        )
        assert run.run_type == "abort"
        assert run.payload == {}


# ── Unit tests: Scheduler enqueue ─────────────────────────────────────────


class TestSchedulerEnqueue:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_enqueue_start(self, mock_db):
        from agent.scheduler import enqueue_start

        sid = uuid.uuid4()
        run = await enqueue_start(mock_db, sid, {"user_message": "hello"})
        assert run.session_id == sid
        assert run.run_type == "start"
        assert run.status == "queued"
        assert run.payload["user_message"] == "hello"
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enqueue_resume(self, mock_db):
        from agent.scheduler import enqueue_resume

        sid = uuid.uuid4()
        decisions = [{"type": "approve"}, {"type": "reject", "message": "no"}]
        run = await enqueue_resume(mock_db, sid, decisions)
        assert run.run_type == "resume"
        assert run.payload["decisions"] == decisions

    @pytest.mark.asyncio
    async def test_enqueue_abort(self, mock_db):
        from agent.scheduler import enqueue_abort

        sid = uuid.uuid4()
        run = await enqueue_abort(mock_db, sid)
        assert run.run_type == "abort"
        assert run.payload == {}


# ── Unit tests: Scheduler status transitions ──────────────────────────────


class TestSchedulerTransitions:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_mark_running(self, mock_db):
        from agent.scheduler import mark_running
        await mark_running(mock_db, uuid.uuid4())
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_completed(self, mock_db):
        from agent.scheduler import mark_completed
        await mark_completed(mock_db, uuid.uuid4())
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_failed(self, mock_db):
        from agent.scheduler import mark_failed
        await mark_failed(mock_db, uuid.uuid4(), "some error")
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_cancelled(self, mock_db):
        from agent.scheduler import mark_cancelled
        await mark_cancelled(mock_db, uuid.uuid4())
        mock_db.execute.assert_awaited_once()


# ── Unit tests: Lease management ──────────────────────────────────────────


class TestLeaseManagement:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_renew_lease(self, mock_db):
        from agent.scheduler import renew_lease
        await renew_lease(mock_db, uuid.uuid4(), 300)
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reclaim_expired(self, mock_db):
        from agent.scheduler import reclaim_expired_runs
        mock_db.execute.return_value = MagicMock(rowcount=2)
        count = await reclaim_expired_runs(mock_db)
        assert count == 2
        mock_db.execute.assert_awaited_once()


# ── Unit tests: Abort detection ───────────────────────────────────────────


class TestAbortDetection:
    @pytest.mark.asyncio
    async def test_has_pending_abort_true(self):
        from agent.scheduler import has_pending_abort

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = uuid.uuid4()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await has_pending_abort(mock_db, uuid.uuid4())
        assert result is True

    @pytest.mark.asyncio
    async def test_has_pending_abort_false(self):
        from agent.scheduler import has_pending_abort

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await has_pending_abort(mock_db, uuid.uuid4())
        assert result is False


# ── Unit tests: Cancel queued runs ────────────────────────────────────────


class TestCancelQueued:
    @pytest.mark.asyncio
    async def test_cancel_queued_runs(self):
        from agent.scheduler import cancel_queued_runs

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=3))
        mock_db.flush = AsyncMock()

        count = await cancel_queued_runs(mock_db, uuid.uuid4())
        assert count == 3


# ── Unit tests: Worker class ──────────────────────────────────────────────


class TestWorkerInit:
    def test_default_worker_id(self):
        from agent.worker import AgentWorker
        w = AgentWorker()
        assert w.worker_id.startswith("worker-")
        assert len(w.worker_id) > 10

    def test_custom_worker_id(self):
        from agent.worker import AgentWorker
        w = AgentWorker(worker_id="test-w1")
        assert w.worker_id == "test-w1"

    def test_shutdown_flag(self):
        from agent.worker import AgentWorker
        w = AgentWorker()
        assert w._shutdown is False
        w.shutdown()
        assert w._shutdown is True


class TestWorkerFailureHandling:
    @pytest.mark.asyncio
    async def test_subtask_continuation_failure_restores_idle(self):
        from agent.worker import AgentWorker

        worker = AgentWorker(worker_id="test-worker")
        worker._execute_start = AsyncMock(side_effect=RuntimeError("boom"))
        worker._bridge_child_failure = AsyncMock()
        worker._publish = AsyncMock()

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.worker.scheduler.mark_failed", new=AsyncMock()),
            patch("agent.executor._update_db_status", new=AsyncMock()) as mock_update_status,
        ):
            await worker._execute_run(
                uuid.uuid4(),
                str(uuid.uuid4()),
                "start",
                {"is_subtask_continuation": True},
            )

        mock_update_status.assert_awaited_once_with(mock_update_status.await_args.args[0], "idle")
        status_event = worker._publish.await_args_list[0].args[1]
        error_event = worker._publish.await_args_list[1].args[1]
        assert status_event == {"event": "status_change", "status": "idle"}
        assert error_event["code"] == "subtask_continuation_error"

    @pytest.mark.asyncio
    async def test_regular_failure_still_marks_error(self):
        from agent.worker import AgentWorker

        worker = AgentWorker(worker_id="test-worker")
        worker._execute_start = AsyncMock(side_effect=RuntimeError("boom"))
        worker._bridge_child_failure = AsyncMock()
        worker._publish = AsyncMock()

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.worker.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.worker.scheduler.mark_failed", new=AsyncMock()),
            patch("agent.executor._update_db_status", new=AsyncMock()) as mock_update_status,
        ):
            await worker._execute_run(
                uuid.uuid4(),
                str(uuid.uuid4()),
                "start",
                {},
            )

        mock_update_status.assert_awaited_once_with(mock_update_status.await_args.args[0], "error")
        status_event = worker._publish.await_args_list[0].args[1]
        error_event = worker._publish.await_args_list[1].args[1]
        assert status_event == {"event": "status_change", "status": "error"}
        assert error_event["code"] == "worker_error"


# ── Unit tests: Executor module separation ────────────────────────────────


class TestExecutorModuleExists:
    def test_executor_imports(self):
        from agent.executor import execute_start, execute_resume
        assert callable(execute_start)
        assert callable(execute_resume)

    def test_executor_helpers(self):
        from agent.executor import _is_tool_error, _extract_token_usage
        assert _is_tool_error(MagicMock(status="error")) is True
        assert _is_tool_error(MagicMock(status="ok", additional_kwargs={})) is False
        assert _extract_token_usage([]) == {"input": 0, "output": 0, "total": 0}

    @pytest.mark.asyncio
    async def test_execute_start_subtask_continuation_uses_internal_human_message(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from agent.executor import (
            _SUBTASK_CONTINUATION_MARKER,
            _SUBTASK_CONTINUATION_PROMPT,
            _SUBTASK_RESULT_BRIDGE_KIND,
            execute_start,
        )

        with (
            patch("agent.executor.build_agent", new=AsyncMock(return_value=MagicMock())),
            patch("agent.executor._execute_graph", new=AsyncMock()) as mock_execute_graph,
        ):
            await execute_start(
                session_id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                user_root="/tmp/user",
                session_dir="/tmp/user/sessions/s1",
                agent_id="build",
                model_id="test-model",
                user_message="[Sub-task completed]\nsummary",
                publish=AsyncMock(),
                is_subtask_continuation=True,
            )

        initial_input = mock_execute_graph.await_args.args[1]
        bridged_msg = initial_input["messages"][0]
        prompt_msg = initial_input["messages"][1]
        assert isinstance(bridged_msg, AIMessage)
        assert bridged_msg.additional_kwargs["agentd_internal"] == _SUBTASK_RESULT_BRIDGE_KIND
        assert isinstance(prompt_msg, HumanMessage)
        assert _SUBTASK_CONTINUATION_MARKER in prompt_msg.content
        assert _SUBTASK_CONTINUATION_PROMPT in prompt_msg.content

    @pytest.mark.asyncio
    async def test_persist_messages_skips_internal_subtask_continuation_messages(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from agent.executor import (
            _SUBTASK_RESULT_BRIDGE_KIND,
            _persist_messages,
        )

        db = AsyncMock()
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        with (
            patch("agent.executor.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.executor.session_svc.count_messages", new=AsyncMock(return_value=0)),
            patch("agent.executor.session_svc.create_message", new=AsyncMock()) as mock_create_message,
        ):
            await _persist_messages(
                str(uuid.uuid4()),
                [
                    MagicMock(),
                    AIMessage(
                        content="[Sub-task completed]\nsummary",
                        additional_kwargs={"agentd_internal": _SUBTASK_RESULT_BRIDGE_KIND},
                    ),
                    HumanMessage(content="[Subtask Continuation - internal only]\n\ncarry on"),
                    AIMessage(content="done"),
                ],
            )

        roles = [call.kwargs["role"] for call in mock_create_message.await_args_list]
        assert roles == ["assistant"]

    @pytest.mark.asyncio
    async def test_execute_resume_child_restores_allowed_tools_from_child_meta(self, tmp_path):
        from agent.child_session import write_child_session_meta
        from agent.executor import execute_resume
        from workspace.manager import ensure_user_root, get_session_dir

        user_root = os.path.join(str(tmp_path), "user")
        ensure_user_root(user_root)

        session_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        session_dir = get_session_dir(user_root, session_id)
        write_child_session_meta(
            session_dir,
            parent_session_id=parent_id,
            parent_session_dir=get_session_dir(user_root, parent_id),
            allowed_tools=["file_write", "bash"],
            resolved_tools=["bash", "file_write"],
        )

        user_id = uuid.uuid4()
        session = MagicMock(
            user_id=user_id,
            parent_id=uuid.UUID(parent_id),
            agent_id="build",
            model_id="test-model",
        )
        user = MagicMock(workspace=user_root)

        db = AsyncMock()
        db.get = AsyncMock(return_value=user)
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = db
        session_ctx.__aexit__.return_value = False

        agent = MagicMock()
        agent.aget_state = AsyncMock(return_value=MagicMock(interrupts=[]))

        with (
            patch("agent.executor.AsyncSessionLocal", return_value=session_ctx),
            patch("agent.executor.session_svc.get_session", new=AsyncMock(return_value=session)),
            patch("agent.executor.build_agent", new=AsyncMock(return_value=agent)) as mock_build_agent,
            patch("agent.executor._execute_graph", new=AsyncMock()) as mock_execute_graph,
        ):
            await execute_resume(
                session_id=session_id,
                decisions=[],
                publish=AsyncMock(),
            )

        assert mock_build_agent.await_args.kwargs["tool_profile"] == "child"
        assert mock_build_agent.await_args.kwargs["allowed_tools"] == ["bash", "file_write"]
        assert mock_execute_graph.await_args.args[4] == session_dir


# ── Unit tests: Runner compatibility shim ─────────────────────────────────


class TestRunnerShim:
    def test_runner_still_exports_start_loop(self):
        from agent.runner import start_loop, resume_loop, abort_loop
        assert callable(start_loop)
        assert callable(resume_loop)
        assert callable(abort_loop)

    def test_runner_still_exports_get_pending(self):
        from agent.runner import get_pending_permissions
        assert get_pending_permissions("nonexistent") == []


# ── Unit tests: Config pool settings ──────────────────────────────────────


class TestConfigPoolSettings:
    def test_default_pool_config(self):
        from core.config import Settings
        s = Settings(database_url="postgresql+asyncpg://u:p@localhost/test")
        assert s.db_pool_size == 10
        assert s.db_max_overflow == 20

    def test_custom_pool_config(self):
        from core.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://u:p@localhost/test",
            db_pool_size=5,
            db_max_overflow=10,
        )
        assert s.db_pool_size == 5
        assert s.db_max_overflow == 10


# ── Unit tests: Event bridge module ───────────────────────────────────────


class TestEventBridgeModule:
    def test_event_bridge_listener_exists(self):
        from core.event_bridge import EventBridgeListener, listener, CHANNEL
        assert isinstance(listener, EventBridgeListener)
        assert CHANNEL == "agentd_events"

    def test_notify_function_exists(self):
        from core.event_bridge import notify
        assert callable(notify)


# ── Unit tests: Permission cancel on abort (#42) ────────────────────────


class TestCancelPendingPermissions:
    @pytest.mark.asyncio
    async def test_cancel_pending_by_session(self):
        from permission.service import cancel_pending_by_session

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=2))

        count = await cancel_pending_by_session(mock_db, uuid.uuid4())
        assert count == 2
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_pending_zero(self):
        from permission.service import cancel_pending_by_session

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=0))

        count = await cancel_pending_by_session(mock_db, uuid.uuid4())
        assert count == 0


# ── Unit tests: Resume idempotency (#P1) ─────────────────────────────────


class TestResumeIdempotency:
    @pytest.mark.asyncio
    async def test_mark_resolved_as_resumed(self):
        """After resume is enqueued, resolved permissions should be marked 'resumed'."""
        from permission.service import mark_resolved_as_resumed

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=3))

        count = await mark_resolved_as_resumed(mock_db, uuid.uuid4())
        assert count == 3
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_resolved_as_resumed_zero(self):
        """If no resolved permissions exist, count should be 0."""
        from permission.service import mark_resolved_as_resumed

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=0))

        count = await mark_resolved_as_resumed(mock_db, uuid.uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_resolve_permission_returns_bool(self):
        """resolve_permission should return False when no pending row is found (race)."""
        from permission.service import resolve_permission

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=0))

        result = await resolve_permission(mock_db, uuid.uuid4(), "approved")
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_permission_success(self):
        """resolve_permission should return True when a pending row is resolved."""
        from permission.service import resolve_permission

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=1))

        result = await resolve_permission(mock_db, uuid.uuid4(), "approved")
        assert result is True


# ── Unit tests: Title think-tag stripping (#P3) ─────────────────────────


class TestStripThinkTags:
    def test_strip_think_block(self):
        from agent.executor import _strip_think_tags
        text = "Hello <think>some reasoning</think> world"
        assert _strip_think_tags(text) == "Hello  world"

    def test_strip_multiline_think_block(self):
        from agent.executor import _strip_think_tags
        text = "<think>\nreasoning\nline 2\n</think>Actual title"
        assert _strip_think_tags(text) == "Actual title"

    def test_strip_standalone_tags(self):
        from agent.executor import _strip_think_tags
        text = "<think>unclosed tag content"
        # Standalone <think> without closing </think> should be removed as tag
        result = _strip_think_tags(text)
        assert "<think>" not in result

    def test_no_think_tags(self):
        from agent.executor import _strip_think_tags
        text = "Normal title without tags"
        assert _strip_think_tags(text) == text

    def test_empty_string(self):
        from agent.executor import _strip_think_tags
        assert _strip_think_tags("") == ""

    def test_only_think_block(self):
        from agent.executor import _strip_think_tags
        text = "<think>all reasoning no content</think>"
        assert _strip_think_tags(text) == ""

    def test_strip_minimax_tool_call(self):
        from agent.executor import _strip_model_tags
        text = "Title <minimax:tool_call>ls -la</minimax:tool_call> here"
        assert _strip_model_tags(text) == "Title  here"

    def test_strip_mixed_model_tags(self):
        from agent.executor import _strip_model_tags
        text = "<think>reasoning</think>Actual <minimax:tool_call>cmd</minimax:tool_call> title"
        assert _strip_model_tags(text) == "Actual  title"

    def test_strip_model_tags_no_tags(self):
        from agent.executor import _strip_model_tags
        text = "Normal title"
        assert _strip_model_tags(text) == "Normal title"


# ── Unit tests: Migration versioning ──────────────────────────────────────


class TestMigrationVersion:
    def test_expected_schema_version_updated(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "016"
