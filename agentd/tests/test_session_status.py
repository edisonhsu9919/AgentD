"""Tests for session status blocking, tool error detection, and token usage extraction.

Covers audit issues #11, #12, #13, #14.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.executor import _is_tool_error, _extract_tool_call_ids, _extract_token_usage


# ── Unit tests: _is_tool_error ──────────────────────────────────────────────


class TestIsToolError:
    def test_error_status(self):
        msg = ToolMessage(content="fail", tool_call_id="tc1", status="error")
        assert _is_tool_error(msg) is True

    def test_success_status(self):
        msg = ToolMessage(content="ok", tool_call_id="tc1")
        assert _is_tool_error(msg) is False

    def test_no_status_attribute(self):
        msg = MagicMock(spec=[])  # no attributes
        assert _is_tool_error(msg) is False

    def test_additional_kwargs_is_error(self):
        msg = MagicMock()
        msg.status = None
        msg.additional_kwargs = {"is_error": True}
        assert _is_tool_error(msg) is True


# ── Unit tests: _extract_tool_call_ids ──────────────────────────────────────


class TestExtractToolCallIds:
    def test_extracts_ids_for_hitl_tools(self):
        ai_msg = AIMessage(content="", tool_calls=[
            {"id": "tc_1", "name": "bash", "args": {"command": "ls"}},
            {"id": "tc_2", "name": "file_read", "args": {"path": "x"}},
            {"id": "tc_3", "name": "file_write", "args": {"path": "y", "content": "z"}},
        ])
        snapshot = MagicMock()
        snapshot.values = {"messages": [ai_msg]}

        ids = _extract_tool_call_ids(snapshot)
        # Only bash and file_write are in _HITL_INTERRUPT_ON, file_read is auto-approved
        assert ids == ["tc_1", "tc_3"]

    def test_empty_when_no_ai_message(self):
        snapshot = MagicMock()
        snapshot.values = {"messages": []}
        assert _extract_tool_call_ids(snapshot) == []

    def test_empty_when_no_tool_calls(self):
        ai_msg = AIMessage(content="just text")
        snapshot = MagicMock()
        snapshot.values = {"messages": [ai_msg]}
        assert _extract_tool_call_ids(snapshot) == []


# ── Unit tests: _extract_token_usage ────────────────────────────────────────


class TestExtractTokenUsage:
    def test_sums_across_multiple_ai_messages(self):
        messages = [
            HumanMessage(content="hi"),
            AIMessage(content="hello", usage_metadata={
                "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            }),
            ToolMessage(content="ok", tool_call_id="tc1"),
            AIMessage(content="done", usage_metadata={
                "input_tokens": 20, "output_tokens": 15, "total_tokens": 35,
            }),
        ]
        result = _extract_token_usage(messages)
        assert result == {"input": 30, "output": 20, "total": 50}

    def test_zero_when_no_usage_metadata(self):
        messages = [AIMessage(content="hello")]
        result = _extract_token_usage(messages)
        assert result == {"input": 0, "output": 0, "total": 0}

    def test_zero_when_empty(self):
        assert _extract_token_usage([]) == {"input": 0, "output": 0, "total": 0}


# ── Integration test: prompt blocked when session is waiting ────────────────


class TestPromptBlocking:
    """Test that POST /api/sessions/{id}/prompt rejects when status is waiting or running."""

    @pytest.fixture
    def mock_session_waiting(self):
        """Create a mock session in 'waiting' status."""
        session = MagicMock()
        session.id = uuid.uuid4()
        session.user_id = uuid.uuid4()
        session.status = "waiting"
        session.agent_id = "build"
        session.model_id = "test-model"
        return session

    @pytest.fixture
    def mock_user(self, mock_session_waiting):
        """Create a mock user that owns the session."""
        user = MagicMock()
        user.id = mock_session_waiting.user_id
        user.username = "testuser"
        user.workspace = "/tmp/test"
        return user

    @pytest.fixture
    def client(self, mock_user, mock_session_waiting):
        """Create a test client with mocked dependencies."""
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        async def override_get_db():
            db = AsyncMock()
            return db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: mock_user

        with patch("session.router.session_svc") as mock_svc:
            mock_svc.get_session = AsyncMock(return_value=mock_session_waiting)
            yield TestClient(app)

        app.dependency_overrides.clear()

    def test_prompt_rejected_when_waiting(self, client, mock_session_waiting):
        """Issue #11: session in 'waiting' status must reject new prompts."""
        response = client.post(
            f"/api/sessions/{mock_session_waiting.id}/prompt",
            json={"content": "hello"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "waiting" in body["error"]["message"].lower() or "permission" in body["error"]["message"].lower()

    def test_prompt_rejected_when_running(self, client, mock_session_waiting):
        """Existing behavior: session in 'running' status rejects new prompts."""
        mock_session_waiting.status = "running"
        response = client.post(
            f"/api/sessions/{mock_session_waiting.id}/prompt",
            json={"content": "hello"},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "CONFLICT"
