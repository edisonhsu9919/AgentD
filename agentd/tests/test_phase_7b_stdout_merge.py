"""Test Phase 7B: Route-level merged stdout/stderr for detached tasks."""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from workspace.manager import get_session_dir


class TestStdoutStderrMerge:
    @pytest.fixture
    def mock_user_and_session(self, tmp_path):
        import session.models  # noqa
        user = AsyncMock()
        user.id = uuid.uuid4()
        user.workspace = str(tmp_path / "workspace")

        sess = AsyncMock()
        sess.id = uuid.uuid4()
        sess.user_id = user.id

        task = AsyncMock()
        task.id = uuid.uuid4()
        task.session_id = sess.id
        
        return user, sess, task

    @pytest.fixture
    def client(self, mock_user_and_session):
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        user, sess, task = mock_user_and_session

        async def override_get_db():
            db = AsyncMock()
            db.get = AsyncMock(return_value=task)
            return db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: user

        with patch("session.router.session_svc") as mock_svc:
            mock_svc.get_session = AsyncMock(return_value=sess)
            yield TestClient(app)

        app.dependency_overrides.clear()

    def test_get_task_stdout_merges_stderr(self, client, mock_user_and_session):
        user, sess, task = mock_user_and_session
        
        # Prepare task output files
        session_dir = get_session_dir(user.workspace, str(sess.id))
        from agent.tasks import init_task_dir
        task_dir = init_task_dir(session_dir, str(task.id))
        
        with open(os.path.join(task_dir, "stderr.log"), "w") as f:
            f.write("progress line 1\nprogress line 2\n")
            
        with open(os.path.join(task_dir, "stdout.log"), "w") as f:
            f.write('{"result": "ok"}\n')

        response = client.get(
            f"/api/sessions/{sess.id}/tasks/{task.id}/stdout"
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "data" in data
        
        stdout_content = data["data"]["stdout"]
        lines = stdout_content.split("\n")
        
        assert len(lines) == 3
        assert lines[0] == "progress line 1"
        assert lines[1] == "progress line 2"
        assert lines[2] == '{"result": "ok"}'
