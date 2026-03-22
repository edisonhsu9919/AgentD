"""Tests for Phase E — planning & todo_update tools + prompt injection.

Covers:
- PlanningTool: create plan, validation, SSE publish, file output
- TodoUpdateTool: update steps, auto-complete, missing plan error
- Prompt injection: active plan injected, inactive plan skipped
- Registry: tools registered with correct permissions
- Task plan API: GET/DELETE endpoints (unit-level)
"""

import json
import os
from unittest.mock import AsyncMock

import pytest

from tools.base import ToolContext


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace for testing."""
    return tmp_path


@pytest.fixture
def ctx(workspace):
    """ToolContext with a mock publish function."""
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=str(workspace),
        session_dir=str(workspace),
        venv_bin=str(workspace / ".venv" / "bin"),
        publish=AsyncMock(),
    )


@pytest.fixture
def ctx_no_publish(workspace):
    """ToolContext without publish (publish=None)."""
    return ToolContext(
        user_id="test-user",
        session_id="test-session",
        user_root=str(workspace),
        session_dir=str(workspace),
        venv_bin=str(workspace / ".venv" / "bin"),
        publish=None,
    )


# ── PlanningTool ─────────────────────────────────────────────────────────────


class TestPlanningTool:
    @pytest.fixture
    def tool(self):
        from tools.planning import PlanningTool
        return PlanningTool()

    @pytest.mark.asyncio
    async def test_create_plan(self, tool, ctx, workspace):
        result = await tool.execute(ctx,
            task_title="Build feature X",
            task_summary="Implement feature X with tests.",
            steps=[
                {"id": "s1", "title": "Read requirements", "status": "pending"},
                {"id": "s2", "title": "Write code", "status": "pending"},
                {"id": "s3", "title": "Write tests", "status": "pending"},
            ],
        )
        assert result["is_error"] is False
        assert "Build feature X" in result["output"]

        # Verify file written
        plan_path = workspace / ".agentd" / "task_plan.json"
        assert plan_path.exists()
        plan = json.loads(plan_path.read_text())
        assert plan["active"] is True
        assert plan["task"]["title"] == "Build feature X"
        assert len(plan["steps"]) == 3
        assert plan["steps"][0]["id"] == "s1"

    @pytest.mark.asyncio
    async def test_publishes_sse_event(self, tool, ctx):
        await tool.execute(ctx,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step 1"}],
        )
        ctx.publish.assert_called_once()
        call_args = ctx.publish.call_args
        assert call_args[0][0] == "test-session"
        assert call_args[0][1]["event"] == "todo_update"

    @pytest.mark.asyncio
    async def test_no_publish_when_none(self, tool, ctx_no_publish):
        result = await tool.execute(ctx_no_publish,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step 1"}],
        )
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_empty_title_rejected(self, tool, ctx):
        result = await tool.execute(ctx,
            task_title="   ",
            steps=[{"id": "s1", "title": "Step"}],
        )
        assert result["is_error"] is True
        assert "empty" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_no_steps_rejected(self, tool, ctx):
        result = await tool.execute(ctx,
            task_title="Task",
            steps=[],
        )
        assert result["is_error"] is True
        assert "step" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_invalid_status_defaults_to_pending(self, tool, ctx, workspace):
        await tool.execute(ctx,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step", "status": "banana"}],
        )
        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["steps"][0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_overwrites_existing_plan(self, tool, ctx, workspace):
        # Create initial plan
        await tool.execute(ctx, task_title="Plan A", steps=[{"id": "s1", "title": "A"}])
        # Overwrite
        await tool.execute(ctx, task_title="Plan B", steps=[{"id": "s1", "title": "B"}])
        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["task"]["title"] == "Plan B"

    @pytest.mark.asyncio
    async def test_auto_generates_ids(self, tool, ctx, workspace):
        await tool.execute(ctx,
            task_title="Task",
            steps=[{"title": "No ID step"}],
        )
        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["steps"][0]["id"] == "s1"


# ── TodoUpdateTool ───────────────────────────────────────────────────────────


class TestTodoUpdateTool:
    @pytest.fixture
    def planning_tool(self):
        from tools.planning import PlanningTool
        return PlanningTool()

    @pytest.fixture
    def tool(self):
        from tools.todo_update import TodoUpdateTool
        return TodoUpdateTool()

    @pytest.mark.asyncio
    async def test_update_step_status(self, tool, planning_tool, ctx, workspace):
        # Create plan first
        await planning_tool.execute(ctx,
            task_title="Task",
            steps=[
                {"id": "s1", "title": "Step 1", "status": "pending"},
                {"id": "s2", "title": "Step 2", "status": "pending"},
            ],
        )

        # Update: s1 completed, s2 in_progress
        result = await tool.execute(ctx, steps=[
            {"id": "s1", "title": "Step 1", "status": "completed"},
            {"id": "s2", "title": "Step 2", "status": "in_progress"},
        ])
        assert result["is_error"] is False
        assert "completed=1" in result["output"]
        assert "in_progress=1" in result["output"]

        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["steps"][0]["status"] == "completed"
        assert plan["steps"][1]["status"] == "in_progress"
        assert plan["active"] is True

    @pytest.mark.asyncio
    async def test_auto_inactive_on_all_completed(self, tool, planning_tool, ctx, workspace):
        await planning_tool.execute(ctx,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step 1", "status": "pending"}],
        )

        result = await tool.execute(ctx, steps=[
            {"id": "s1", "title": "Step 1", "status": "completed"},
        ])
        assert result["is_error"] is False
        assert "inactive" in result["output"]

        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["active"] is False

    @pytest.mark.asyncio
    async def test_explicit_active_false(self, tool, planning_tool, ctx, workspace):
        await planning_tool.execute(ctx,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step 1", "status": "pending"}],
        )

        result = await tool.execute(ctx,
            active=False,
            steps=[{"id": "s1", "title": "Step 1", "status": "pending"}],
        )
        assert result["is_error"] is False
        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["active"] is False

    @pytest.mark.asyncio
    async def test_no_plan_exists_error(self, tool, ctx):
        result = await tool.execute(ctx, steps=[
            {"id": "s1", "title": "Step 1", "status": "completed"},
        ])
        assert result["is_error"] is True
        assert "planning" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_publishes_sse_event(self, tool, planning_tool, ctx):
        await planning_tool.execute(ctx,
            task_title="Task",
            steps=[{"id": "s1", "title": "Step 1"}],
        )
        ctx.publish.reset_mock()

        await tool.execute(ctx, steps=[
            {"id": "s1", "title": "Step 1", "status": "completed"},
        ])
        ctx.publish.assert_called_once()
        assert ctx.publish.call_args[0][1]["event"] == "todo_update"

    @pytest.mark.asyncio
    async def test_preserves_task_metadata(self, tool, planning_tool, ctx, workspace):
        """todo_update should not erase the task title/summary."""
        await planning_tool.execute(ctx,
            task_title="Important Task",
            task_summary="Very important.",
            steps=[{"id": "s1", "title": "Step 1"}],
        )

        await tool.execute(ctx, steps=[
            {"id": "s1", "title": "Step 1", "status": "completed"},
        ])

        plan = json.loads((workspace / ".agentd" / "task_plan.json").read_text())
        assert plan["task"]["title"] == "Important Task"
        assert plan["task"]["summary"] == "Very important."


# ── Prompt injection layer ───────────────────────────────────────────────────


class TestTaskPlanPromptInjection:
    def _write_plan(self, session_dir, plan):
        agentd_dir = os.path.join(session_dir, ".agentd")
        os.makedirs(agentd_dir, exist_ok=True)
        with open(os.path.join(agentd_dir, "task_plan.json"), "w") as f:
            json.dump(plan, f)

    def test_active_plan_injected(self, tmp_path):
        from agent.runtime import _load_task_plan_layer
        self._write_plan(str(tmp_path), {
            "active": True,
            "task": {"title": "Build X", "summary": "Build feature X."},
            "steps": [
                {"id": "s1", "status": "completed", "title": "Read docs", "detail": ""},
                {"id": "s2", "status": "in_progress", "title": "Write code", "detail": "Implement the parser."},
                {"id": "s3", "status": "pending", "title": "Test", "detail": ""},
            ],
        })
        result = _load_task_plan_layer(str(tmp_path))
        assert "Build X" in result
        assert "[x] Read docs" in result
        assert "[>] Write code" in result
        assert "[ ] Test" in result
        assert "Implement the parser" in result

    def test_inactive_plan_not_injected(self, tmp_path):
        from agent.runtime import _load_task_plan_layer
        self._write_plan(str(tmp_path), {
            "active": False,
            "task": {"title": "Done"},
            "steps": [],
        })
        result = _load_task_plan_layer(str(tmp_path))
        assert result == ""

    def test_no_plan_file(self, tmp_path):
        from agent.runtime import _load_task_plan_layer
        result = _load_task_plan_layer(str(tmp_path))
        assert result == ""

    def test_corrupt_json(self, tmp_path):
        from agent.runtime import _load_task_plan_layer
        agentd_dir = os.path.join(str(tmp_path), ".agentd")
        os.makedirs(agentd_dir)
        with open(os.path.join(agentd_dir, "task_plan.json"), "w") as f:
            f.write("{bad json")
        result = _load_task_plan_layer(str(tmp_path))
        assert result == ""

    def test_only_in_progress_detail_injected(self, tmp_path):
        from agent.runtime import _load_task_plan_layer
        self._write_plan(str(tmp_path), {
            "active": True,
            "task": {"title": "Task"},
            "steps": [
                {"id": "s1", "status": "completed", "title": "Done step", "detail": "Should NOT appear in detail section."},
                {"id": "s2", "status": "in_progress", "title": "Current step", "detail": "Should appear."},
            ],
        })
        result = _load_task_plan_layer(str(tmp_path))
        # The detail for completed step should NOT be in the "Current Step Detail" section
        assert "Should appear" in result
        # completed step detail is NOT injected (only in_progress detail is)
        lines_after_detail_header = result.split("### Current Step Detail")
        if len(lines_after_detail_header) > 1:
            detail_section = lines_after_detail_header[1]
            assert "Should NOT appear" not in detail_section


# ── Registry integration ────────────────────────────────────────────────────


class TestRegistryPlanningTools:
    def test_tools_registered(self):
        from tools.registry import get_registry
        registry = get_registry()
        assert registry.get("planning") is not None
        assert registry.get("todo_update") is not None

    def test_auto_allow_permissions(self):
        from tools.registry import get_registry
        registry = get_registry()
        assert registry.default_permission("planning") == "allow"
        assert registry.default_permission("todo_update") == "allow"

    def test_not_in_hitl_interrupt(self):
        from agent.runtime import _HITL_INTERRUPT_ON
        assert "planning" not in _HITL_INTERRUPT_ON
        assert "todo_update" not in _HITL_INTERRUPT_ON
