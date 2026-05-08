"""v0.4.7 Phase D background task error isolation tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import json

import pytest


class _EmptyStream:
    async def readline(self):
        return b""


class _FailedProcess:
    def __init__(self):
        self.stdout = _EmptyStream()
        self.stderr = _EmptyStream()
        self.returncode = 7

    async def wait(self):
        return self.returncode


class _ExplodingStream:
    async def readline(self):
        raise RuntimeError("stream exploded")


class _MonitorExceptionProcess:
    def __init__(self):
        self.stdout = _ExplodingStream()
        self.stderr = _EmptyStream()
        self.returncode = None

    async def wait(self):
        return 0


@pytest.mark.asyncio
async def test_detached_process_failure_updates_task_not_session(tmp_path):
    from tools.launch_detached import _monitor_process

    publish = AsyncMock()
    (tmp_path / "panel_content.json").write_text(json.dumps({"type": "structured"}))
    with (
        patch("agent.tasks.update_task_status") as update_task_status,
        patch("agent.tasks.write_task_result") as write_task_result,
        patch("tools.launch_detached._update_db_status") as update_db_status,
    ):
        await _monitor_process(
            process=_FailedProcess(),  # type: ignore[arg-type]
            session_id="session-1",
            session_dir=str(tmp_path),
            task_id="task-1",
            stdout_path=str(tmp_path / "stdout.log"),
            stderr_path=str(tmp_path / "stderr.log"),
            publish=publish,
        )

    update_task_status.assert_called_once()
    assert update_task_status.call_args.args[:3] == (str(tmp_path), "task-1", "failed")
    write_task_result.assert_called_once()
    update_db_status.assert_called_once_with("session-1", "task-1", "failed", "Process exited with code 7")
    events = [call.args[1] for call in publish.await_args_list]
    assert {"event": "task_started", "task_id": "task-1", "status": "running"} in events
    assert any(event.get("event") == "task_failed" and event.get("status") == "failed" for event in events)


@pytest.mark.asyncio
async def test_detached_monitor_exception_publishes_task_failed(tmp_path):
    from tools.launch_detached import _monitor_process

    publish = AsyncMock()
    (tmp_path / "panel_content.json").write_text(json.dumps({"type": "structured"}))
    with (
        patch("agent.tasks.update_task_status") as update_task_status,
        patch("agent.tasks.write_task_result") as write_task_result,
        patch("tools.launch_detached._update_db_status") as update_db_status,
    ):
        await _monitor_process(
            process=_MonitorExceptionProcess(),  # type: ignore[arg-type]
            session_id="session-2",
            session_dir=str(tmp_path),
            task_id="task-2",
            stdout_path=str(tmp_path / "stdout.log"),
            stderr_path=str(tmp_path / "stderr.log"),
            publish=publish,
        )

    update_task_status.assert_called_once_with(
        str(tmp_path),
        "task-2",
        "failed",
        error="stream exploded",
    )
    write_task_result.assert_called_once()
    assert write_task_result.call_args.args[2]["source"] == "monitor_exception"
    update_db_status.assert_called_once_with("session-2", "task-2", "failed", "stream exploded")
    events = [call.args[1] for call in publish.await_args_list]
    assert any(
        event.get("event") == "task_failed"
        and event.get("source") == "monitor_exception"
        and event.get("error") == "stream exploded"
        for event in events
    )
