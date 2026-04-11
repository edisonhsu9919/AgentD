"""Tests for Phase A — state recovery and truth-based interfaces.

Covers:
- GET /api/sessions/{id}/runtime
- GET /api/sessions/{id}/permissions/pending
- RuntimeResponse schema derivation
- get_last_message_seq service helper
- Truth-before-event ordering in runner
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from session.schemas import RuntimeResponse


# ── Unit tests: RuntimeResponse schema ──────────────────────────────────────


class TestRuntimeResponseSchema:
    def test_idle_session(self):
        now = datetime.now(timezone.utc)
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="idle",
            phase=None,
            last_message_seq=5,
            pending_permissions_count=0,
            resumable=False,
            last_error=None,
            updated_at=now,
        )
        assert r.status == "idle"
        assert r.phase is None
        assert r.resumable is False
        assert r.pending_permissions_count == 0

    def test_waiting_session(self):
        now = datetime.now(timezone.utc)
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="waiting",
            phase="permission_waiting",
            last_message_seq=3,
            pending_permissions_count=2,
            resumable=True,
            last_error=None,
            updated_at=now,
        )
        assert r.status == "waiting"
        assert r.phase == "permission_waiting"
        assert r.resumable is True
        assert r.pending_permissions_count == 2

    def test_running_session(self):
        now = datetime.now(timezone.utc)
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="running",
            phase="running",
            last_message_seq=1,
            pending_permissions_count=0,
            resumable=False,
            last_error=None,
            updated_at=now,
        )
        assert r.status == "running"
        assert r.phase == "running"
        assert r.resumable is False

    def test_error_session(self):
        now = datetime.now(timezone.utc)
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="error",
            phase="error",
            last_message_seq=4,
            pending_permissions_count=0,
            resumable=False,
            last_error=None,
            updated_at=now,
        )
        assert r.status == "error"
        assert r.phase == "error"

    def test_queued_session(self):
        now = datetime.now(timezone.utc)
        r = RuntimeResponse(
            session_id=uuid.uuid4(),
            status="queued",
            phase="queued",
            last_message_seq=1,
            pending_permissions_count=0,
            resumable=False,
            last_error=None,
            updated_at=now,
        )
        assert r.status == "queued"
        assert r.phase == "queued"
        assert r.resumable is False

    def test_serialization_roundtrip(self):
        now = datetime.now(timezone.utc)
        sid = uuid.uuid4()
        r = RuntimeResponse(
            session_id=sid,
            status="waiting",
            phase="permission_waiting",
            last_message_seq=10,
            pending_permissions_count=1,
            resumable=True,
            last_error=None,
            updated_at=now,
        )
        data = r.model_dump(mode="json")
        assert data["session_id"] == str(sid)
        assert data["status"] == "waiting"
        assert data["pending_permissions_count"] == 1
        assert data["resumable"] is True


# ── Unit tests: PendingPermissionResponse schema ────────────────────────────


class TestPendingPermissionResponseSchema:
    def test_basic_fields(self):
        from permission.schemas import PendingPermissionResponse

        now = datetime.now(timezone.utc)
        r = PendingPermissionResponse(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            tool_call_id="tc_123",
            tool_name="bash",
            input={"command": "ls -la"},
            status="pending",
            created_at=now,
        )
        assert r.tool_name == "bash"
        assert r.status == "pending"
        assert r.input == {"command": "ls -la"}

    def test_nullable_tool_call_id(self):
        from permission.schemas import PendingPermissionResponse

        r = PendingPermissionResponse(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            tool_call_id=None,
            tool_name="file_write",
            input={"path": "x.py", "content": "hello"},
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        assert r.tool_call_id is None


# ── Integration tests: /runtime and /permissions/pending endpoints ──────────


class TestRuntimeEndpoint:
    """Test GET /api/sessions/{id}/runtime."""

    @pytest.fixture
    def setup(self):
        """Set up mock session, user, and FastAPI test client."""
        sid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        session = MagicMock()
        session.id = sid
        session.user_id = uid
        session.status = "idle"
        session.updated_at = now

        user = MagicMock()
        user.id = uid

        return session, user, sid

    @pytest.fixture
    def client(self, setup):
        session, user, sid = setup
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        async def override_get_db():
            return AsyncMock()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: user

        with (
            patch("session.router.session_svc") as mock_session_svc,
            patch("session.router.perm_svc", create=True) as mock_perm_svc,
        ):
            mock_session_svc.get_session = AsyncMock(return_value=session)
            mock_session_svc.get_last_message_seq = AsyncMock(return_value=5)
            # Patch the import inside the endpoint
            with patch("permission.service.count_pending_by_session", new_callable=AsyncMock, return_value=0):
                yield TestClient(app), session, sid

        app.dependency_overrides.clear()

    def test_runtime_idle(self, client):
        test_client, session, sid = client
        session.status = "idle"

        # We need to patch at the right level since the endpoint does local imports
        with patch("permission.service.count_pending_by_session", new_callable=AsyncMock, return_value=0):
            response = test_client.get(f"/api/sessions/{sid}/runtime")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "idle"
        assert data["phase"] is None
        assert data["resumable"] is False
        assert data["pending_permissions_count"] == 0

    def test_runtime_waiting(self, client):
        test_client, session, sid = client
        session.status = "waiting"

        with patch("permission.service.count_pending_by_session", new_callable=AsyncMock, return_value=2):
            response = test_client.get(f"/api/sessions/{sid}/runtime")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "waiting"
        assert data["phase"] == "permission_waiting"
        assert data["resumable"] is True
        assert data["pending_permissions_count"] == 2

    def test_runtime_not_found(self, client):
        test_client, session, sid = client
        # Simulate session not found by returning None
        with patch("session.router.session_svc") as mock_svc:
            mock_svc.get_session = AsyncMock(return_value=None)
            response = test_client.get(f"/api/sessions/{uuid.uuid4()}/runtime")
        assert response.status_code == 404

    def test_runtime_surfaces_last_failed_run_error(self, setup):
        session, user, sid = setup
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        db = AsyncMock()
        diag_run = MagicMock()
        diag_run.diagnostics = {
            "last_call_prompt_tokens": 12,
            "last_call_completion_tokens": 3,
            "context_window_limit": 1000,
            "context_usage_ratio": 0.1,
        }
        failed_run = MagicMock()
        failed_run.error = "subtask continuation failed"
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=diag_run)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=failed_run)),
            count_result,
        ])

        async def override_get_db():
            return db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: user

        with (
            patch("session.router.session_svc") as mock_session_svc,
            patch("permission.service.count_pending_by_session", new_callable=AsyncMock, return_value=0),
        ):
            mock_session_svc.get_session = AsyncMock(return_value=session)
            mock_session_svc.get_last_message_seq = AsyncMock(return_value=5)
            client = TestClient(app)
            response = client.get(f"/api/sessions/{sid}/runtime")

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["last_error"] == "subtask continuation failed"


class TestPendingPermissionsEndpoint:
    """Test GET /api/sessions/{id}/permissions/pending."""

    @pytest.fixture
    def setup(self):
        sid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        session = MagicMock()
        session.id = sid
        session.user_id = uid
        session.status = "waiting"
        session.updated_at = now

        user = MagicMock()
        user.id = uid

        # Create mock permission requests
        perm1 = MagicMock()
        perm1.id = uuid.uuid4()
        perm1.session_id = sid
        perm1.tool_call_id = "tc_1"
        perm1.tool_name = "bash"
        perm1.input = {"command": "rm -rf test/"}
        perm1.status = "pending"
        perm1.created_at = now

        return session, user, sid, [perm1]

    @pytest.fixture
    def client(self, setup):
        session, user, sid, pending = setup
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        async def override_get_db():
            return AsyncMock()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: user

        with patch("session.router.session_svc") as mock_session_svc:
            mock_session_svc.get_session = AsyncMock(return_value=session)
            with patch("permission.service.get_pending_by_session", new_callable=AsyncMock, return_value=pending):
                yield TestClient(app), session, sid, pending

        app.dependency_overrides.clear()

    def test_pending_returns_list(self, client):
        test_client, session, sid, pending = client

        with patch("permission.service.get_pending_by_session", new_callable=AsyncMock, return_value=pending):
            response = test_client.get(f"/api/sessions/{sid}/permissions/pending")

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total"] == 1
        assert body["data"][0]["tool_name"] == "bash"
        assert body["data"][0]["status"] == "pending"

    def test_pending_empty_when_idle(self, client):
        test_client, session, sid, _ = client
        session.status = "idle"

        with patch("permission.service.get_pending_by_session", new_callable=AsyncMock, return_value=[]):
            response = test_client.get(f"/api/sessions/{sid}/permissions/pending")

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total"] == 0
        assert body["data"] == []


# ── Unit test: runner truth-before-event ordering ───────────────────────────


class TestRunnerTruthBeforeEvent:
    """Verify that _run_resume updates DB before publishing SSE (Phase C shim)."""

    def test_resume_updates_db_before_sse(self):
        """The _run_resume function should call _update_db_status('running')
        before publishing status_change SSE. This is verified by reading the
        source code structure."""
        import inspect
        from agent.runner import _run_resume

        source = inspect.getsource(_run_resume)
        # Find positions of the DB update and SSE publish
        db_update_pos = source.find("_update_db_status(session_id, \"running\")")
        sse_publish_pos = source.find("\"status\": \"running\"")

        assert db_update_pos != -1, "_update_db_status call not found in _run_resume"
        assert sse_publish_pos != -1, "SSE publish not found in _run_resume"
        assert db_update_pos < sse_publish_pos, (
            "DB update must come before SSE publish in _run_resume"
        )
