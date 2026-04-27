"""Tests for deleting parent sessions with child-session trees."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from session import service as session_svc


class TestDeleteSessionRouteCascade:
    @pytest.fixture
    def ids(self):
        return SimpleNamespace(
            user_id=uuid.uuid4(),
            parent_id=uuid.uuid4(),
            child_id=uuid.uuid4(),
        )

    def test_delete_parent_session_deletes_child_sessions(self, ids, tmp_path):
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        db = AsyncMock()
        user_root = tmp_path / "workspace"
        for session_id in (ids.parent_id, ids.child_id):
            session_dir = user_root / "sessions" / str(session_id)
            session_dir.mkdir(parents=True)
            (session_dir / "artifact.txt").write_text("x")

        current_user = SimpleNamespace(id=ids.user_id, workspace=str(user_root))
        parent = SimpleNamespace(
            id=ids.parent_id,
            user_id=ids.user_id,
            status="idle",
        )
        delete_result = session_svc.DeleteSessionTreeResult(
            deleted_session_ids=[ids.parent_id, ids.child_id]
        )

        async def override_get_db():
            return db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: current_user
        try:
            with (
                patch(
                    "session.router.session_svc.get_session",
                    new=AsyncMock(return_value=parent),
                ),
                patch(
                    "session.router.session_svc.delete_session_tree",
                    new=AsyncMock(return_value=delete_result),
                ) as mock_delete_tree,
            ):
                client = TestClient(app)
                response = client.delete(f"/api/sessions/{ids.parent_id}")

            assert response.status_code == 200
            body = response.json()["data"]
            assert body["deleted"] is True
            assert body["deleted_session_ids"] == [
                str(ids.parent_id),
                str(ids.child_id),
            ]
            assert body["deleted_count"] == 2
            mock_delete_tree.assert_awaited_once_with(
                db,
                ids.parent_id,
                ids.user_id,
            )
            db.commit.assert_awaited_once()
            assert not (user_root / "sessions" / str(ids.parent_id)).exists()
            assert not (user_root / "sessions" / str(ids.child_id)).exists()
        finally:
            app.dependency_overrides.clear()

    def test_delete_parent_session_rejects_when_child_running(self, ids, tmp_path):
        from api.deps import get_current_user
        from core.database import get_db
        from main import app

        db = AsyncMock()
        current_user = SimpleNamespace(id=ids.user_id, workspace=str(tmp_path))
        parent = SimpleNamespace(
            id=ids.parent_id,
            user_id=ids.user_id,
            status="idle",
        )

        async def override_get_db():
            return db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: current_user
        try:
            with (
                patch(
                    "session.router.session_svc.get_session",
                    new=AsyncMock(return_value=parent),
                ),
                patch(
                    "session.router.session_svc.delete_session_tree",
                    new=AsyncMock(
                        side_effect=session_svc.SessionTreeBusyError([ids.child_id])
                    ),
                ),
                patch("session.router._cleanup_deleted_session_dirs") as mock_cleanup,
            ):
                client = TestClient(app)
                response = client.delete(f"/api/sessions/{ids.parent_id}")

            assert response.status_code == 409
            body = response.json()["error"]
            assert body["code"] == "SESSION_BUSY"
            assert body["blocking_session_ids"] == [str(ids.child_id)]
            db.commit.assert_not_awaited()
            mock_cleanup.assert_not_called()
        finally:
            app.dependency_overrides.clear()


class TestDeleteSessionTreeService:
    @pytest.mark.asyncio
    async def test_delete_session_tree_deletes_leaf_to_root(self):
        db = AsyncMock()
        user_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        grandchild_id = uuid.uuid4()
        tree = [
            SimpleNamespace(id=parent_id, user_id=user_id, status="idle"),
            SimpleNamespace(id=child_id, user_id=user_id, status="idle"),
            SimpleNamespace(id=grandchild_id, user_id=user_id, status="idle"),
        ]
        deleted_ids = []

        async def fake_delete_row(_db, session_id):
            deleted_ids.append(session_id)
            return 1

        with (
            patch(
                "session.service.collect_session_tree",
                new=AsyncMock(return_value=tree),
            ),
            patch("session.service._delete_session_row", new=fake_delete_row),
        ):
            result = await session_svc.delete_session_tree(db, parent_id, user_id)

        assert result.deleted_session_ids == [parent_id, child_id, grandchild_id]
        assert result.deleted_count == 3
        assert deleted_ids == [grandchild_id, child_id, parent_id]

    @pytest.mark.asyncio
    async def test_delete_session_tree_rejects_busy_child(self):
        db = AsyncMock()
        user_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        tree = [
            SimpleNamespace(id=parent_id, user_id=user_id, status="idle"),
            SimpleNamespace(id=child_id, user_id=user_id, status="running"),
        ]

        with (
            patch(
                "session.service.collect_session_tree",
                new=AsyncMock(return_value=tree),
            ),
            patch("session.service._delete_session_row", new=AsyncMock()) as mock_delete,
        ):
            with pytest.raises(session_svc.SessionTreeBusyError) as exc_info:
                await session_svc.delete_session_tree(db, parent_id, user_id)

        assert exc_info.value.blocking_session_ids == [child_id]
        mock_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_session_tree_rejects_other_user_descendant(self):
        db = AsyncMock()
        user_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        tree = [
            SimpleNamespace(id=parent_id, user_id=user_id, status="idle"),
            SimpleNamespace(id=child_id, user_id=other_user_id, status="idle"),
        ]

        with (
            patch(
                "session.service.collect_session_tree",
                new=AsyncMock(return_value=tree),
            ),
            patch("session.service._delete_session_row", new=AsyncMock()) as mock_delete,
        ):
            with pytest.raises(session_svc.SessionTreeOwnershipError):
                await session_svc.delete_session_tree(db, parent_id, user_id)

        mock_delete.assert_not_awaited()
