"""Phase P3 — Long-Run Workbench tests.

Tests cover:
- Task output persistence (file structure, meta.json, helpers)
- session_tasks ORM model
- launch_detached_process tool metadata & schema
- launch_subagent tool metadata & schema
- Registry tool profile filtering
- Tool count with new tools
"""

import json
import os
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.base import ToolContext, ToolMetadata
from tools.registry import get_registry
from workspace.manager import ensure_user_root, get_session_dir


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def user_root(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    return root


@pytest.fixture
def session_dir(user_root):
    return get_session_dir(user_root, "test-session")


def _make_ctx(session_dir: str) -> ToolContext:
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=os.path.dirname(os.path.dirname(session_dir)),
        session_dir=session_dir,
        venv_bin="",
        publish=AsyncMock(),
    )


# ── Task output persistence ─────────────────────────────────────────────


class TestTaskPersistence:
    def test_init_task_dir(self, session_dir):
        from agent.tasks import init_task_dir
        task_dir = init_task_dir(session_dir, "task-001")

        assert os.path.isdir(task_dir)
        assert os.path.isdir(os.path.join(task_dir, "artifacts"))
        assert os.path.isfile(os.path.join(task_dir, "stdout.log"))
        assert os.path.isfile(os.path.join(task_dir, "stderr.log"))

    def test_write_and_read_meta(self, session_dir):
        from agent.tasks import init_task_dir, write_task_meta, read_task_meta

        init_task_dir(session_dir, "task-002")
        meta = write_task_meta(
            session_dir, "task-002",
            session_id="sess-1",
            task_kind="process",
            blocking_mode="detached",
            status="running",
            title="Test Task",
            command="echo hello",
        )

        assert meta["task_id"] == "task-002"
        assert meta["task_kind"] == "process"
        assert meta["blocking_mode"] == "detached"
        assert meta["status"] == "running"

        # Read back
        loaded = read_task_meta(session_dir, "task-002")
        assert loaded["title"] == "Test Task"
        assert loaded["command"] == "echo hello"

    def test_update_task_status(self, session_dir):
        from agent.tasks import init_task_dir, write_task_meta, update_task_status

        init_task_dir(session_dir, "task-003")
        write_task_meta(
            session_dir, "task-003",
            session_id="sess-1",
            task_kind="process",
            blocking_mode="detached",
        )

        updated = update_task_status(session_dir, "task-003", "completed")
        assert updated["status"] == "completed"

    def test_update_task_with_error(self, session_dir):
        from agent.tasks import init_task_dir, write_task_meta, update_task_status

        init_task_dir(session_dir, "task-004")
        write_task_meta(
            session_dir, "task-004",
            session_id="sess-1",
            task_kind="process",
            blocking_mode="detached",
        )

        updated = update_task_status(
            session_dir, "task-004", "failed", error="exit code 1",
        )
        assert updated["status"] == "failed"
        assert updated["error"] == "exit code 1"

    def test_update_nonexistent_task(self, session_dir):
        from agent.tasks import update_task_status
        assert update_task_status(session_dir, "nonexistent", "completed") is None

    def test_list_tasks(self, session_dir):
        from agent.tasks import init_task_dir, write_task_meta, list_tasks

        for i in range(3):
            tid = f"task-{i:03d}"
            init_task_dir(session_dir, tid)
            write_task_meta(
                session_dir, tid,
                session_id="sess-1",
                task_kind="process",
                blocking_mode="detached",
                title=f"Task {i}",
            )

        tasks = list_tasks(session_dir)
        assert len(tasks) == 3

    def test_list_tasks_empty(self, session_dir):
        from agent.tasks import list_tasks
        assert list_tasks(session_dir) == []

    def test_write_task_result(self, session_dir):
        from agent.tasks import init_task_dir, write_task_result

        init_task_dir(session_dir, "task-005")
        write_task_result(session_dir, "task-005", {
            "returncode": 0,
            "status": "completed",
        })

        result_path = os.path.join(
            session_dir, ".agentd/tasks/task-005/result.json"
        )
        assert os.path.isfile(result_path)
        with open(result_path) as f:
            data = json.load(f)
        assert data["returncode"] == 0

    def test_read_task_stdout(self, session_dir):
        from agent.tasks import init_task_dir, read_task_stdout

        task_dir = init_task_dir(session_dir, "task-006")
        with open(os.path.join(task_dir, "stdout.log"), "w") as f:
            for i in range(10):
                f.write(f"line {i}\n")

        output = read_task_stdout(session_dir, "task-006", tail_lines=3)
        lines = output.strip().split("\n")
        assert len(lines) == 3
        assert "line 9" in lines[-1]


# ── Tool metadata ────────────────────────────────────────────────────────


class TestLaunchDetachedMetadata:
    def test_metadata(self):
        from tools.launch_detached import LaunchDetachedProcessTool
        tool = LaunchDetachedProcessTool()
        meta = tool.metadata
        assert meta.default_permission == "ask"
        assert meta.is_read_only is False
        assert meta.can_run_in_background is True
        assert meta.mutates_session_state is True
        assert meta.access_scope == "session_only"

    def test_schema(self):
        from tools.launch_detached import LaunchDetachedProcessTool
        tool = LaunchDetachedProcessTool()
        schema = tool.schema()
        assert "command" in schema["properties"]
        assert "title" in schema["properties"]
        assert "command" in schema["required"]


class TestLaunchSubagentMetadata:
    def test_metadata(self):
        from tools.launch_subagent import LaunchSubagentTool
        tool = LaunchSubagentTool()
        meta = tool.metadata
        assert meta.default_permission == "ask"
        assert meta.is_read_only is False
        assert meta.can_run_in_background is False
        assert meta.mutates_session_state is True

    def test_schema(self):
        from tools.launch_subagent import LaunchSubagentTool
        tool = LaunchSubagentTool()
        schema = tool.schema()
        assert "task_packet" in schema["properties"]
        assert "allowed_tools" in schema["properties"]
        assert "task_packet" in schema["required"]


# ── Registry tool count and profiles ─────────────────────────────────────


class TestRegistryP3:
    def test_tool_count_is_13(self):
        registry = get_registry()
        assert len(registry.tools) == 16

    def test_new_tools_registered(self):
        registry = get_registry()
        assert "launch_detached_process" in registry.tools
        assert "launch_subagent" in registry.tools

    def test_child_profile_excludes_spawn_tools(self):
        registry = get_registry()
        ctx = _make_ctx("/tmp/fake")
        child_tools = registry.get_langchain_tools(ctx, tool_profile="child")
        child_names = {t.name for t in child_tools}

        assert "launch_detached_process" not in child_names
        assert "launch_subagent" not in child_names

    def test_child_profile_includes_read_only_tools(self):
        registry = get_registry()
        ctx = _make_ctx("/tmp/fake")
        child_tools = registry.get_langchain_tools(ctx, tool_profile="child")
        child_names = {t.name for t in child_tools}

        assert "file_read" in child_names
        assert "file_inspect" in child_names
        assert "list_dir" in child_names
        assert "glob" in child_names
        assert "grep" in child_names

    def test_child_profile_default_excludes_write_tools(self):
        registry = get_registry()
        ctx = _make_ctx("/tmp/fake")
        child_tools = registry.get_langchain_tools(ctx, tool_profile="child")
        child_names = {t.name for t in child_tools}

        assert "file_write" not in child_names
        assert "file_edit" not in child_names
        assert "bash" not in child_names

    def test_child_profile_with_allowed_tools(self):
        registry = get_registry()
        ctx = _make_ctx("/tmp/fake")
        child_tools = registry.get_langchain_tools(
            ctx, tool_profile="child",
            allowed_tools={"file_write", "bash"},
        )
        child_names = {t.name for t in child_tools}

        assert "file_write" in child_names
        assert "bash" in child_names
        # Still can't spawn
        assert "launch_subagent" not in child_names

    def test_full_profile(self):
        registry = get_registry()
        ctx = _make_ctx("/tmp/fake")
        full_tools = registry.get_langchain_tools(ctx, tool_profile=None)
        assert len(full_tools) == 16


# ── SessionTask ORM model ───────────────────────────────────────────────


class TestSessionTaskModel:
    def test_model_importable(self):
        from agent.task_models import SessionTask
        assert SessionTask.__tablename__ == "session_tasks"

    def test_model_columns(self):
        from agent.task_models import SessionTask
        columns = {c.name for c in SessionTask.__table__.columns}
        expected = {
            "id", "session_id", "spawned_by_tool", "tool_call_id",
            "task_kind", "blocking_mode", "status", "title", "command",
            "child_session_id", "pid", "stdout_path", "stderr_path",
            "artifact_root", "result_ref", "error", "created_at", "updated_at",
        }
        assert expected.issubset(columns)


# ── ORM FK resolution (regression guard for live FK error) ───────────────


class TestORMFKResolution:
    """Guard against SQLAlchemy FK resolution errors at import time.

    The live failure was: 'Foreign key associated with column sessions.user_id
    could not find table users'. This happens when session.models is imported
    without auth.models being registered first.
    """

    def test_session_task_fk_resolution(self):
        """SessionTask → sessions FK resolves without error."""
        import session.models  # noqa: F401
        import auth.models  # noqa: F401
        from agent.task_models import SessionTask
        # If we get here without error, FK resolution succeeded
        assert SessionTask.__tablename__ == "session_tasks"

    def test_subagent_import_chain(self):
        """launch_subagent's import chain doesn't trigger FK errors."""
        # Simulate the import chain that happens inside _create_child_session
        import auth.models  # noqa: F401
        import session.models  # noqa: F401
        from session import service as session_svc
        assert callable(session_svc.create_session)

    def test_session_model_has_parent_id(self):
        """sessions table has parent_id for child session support."""
        from session.models import Session
        columns = {c.name for c in Session.__table__.columns}
        assert "parent_id" in columns


# ── Migration ────────────────────────────────────────────────────────────


class TestMigration014:
    def test_expected_schema_version(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "014"

    def test_migration_file_exists(self):
        from pathlib import Path
        migration = Path(__file__).parent.parent / "db" / "alembic" / "versions" / "014_session_tasks.py"
        assert migration.exists()
