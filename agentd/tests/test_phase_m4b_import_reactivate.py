"""Phase M4-B — Import-local reactivation after soft-delete tests (P1 Codex audit).

Tests cover:
- import-local with no prior record → creates new
- import-local with active record → 409 conflict
- import-local with inactive (soft-deleted) record → reactivates + updates
"""

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from skills.router import router as skills_router


# ── App fixture ──────────────────────────────────────────────────────────────

def _build_test_app():
    app = FastAPI()
    app.include_router(skills_router, prefix="/api/skills")
    return app


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config.settings, "workspace_root", str(tmp_path))
    return str(tmp_path)


def _make_admin():
    return MagicMock(
        id=uuid.uuid4(),
        workspace="/tmp/test-admin",
        is_admin=True,
    )


def _make_skill_record(name, version, is_active=True):
    mock = MagicMock()
    mock.id = uuid.uuid4()
    mock.name = name
    mock.version = version
    mock.description = "test"
    mock.content = "body"
    mock.tags = ["test"]
    mock.icon = ""
    mock.license = ""
    mock.compatibility = ""
    mock.metadata_extra = {}
    mock.is_active = is_active
    mock.source_type = "import_local"
    mock.source_path = "/some/path"
    mock.created_by = uuid.uuid4()
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.install_count = 0
    return mock


# ── Test: import-local conflict and reactivation ─────────────────────────────


class TestImportLocalReactivation:

    @pytest.mark.asyncio
    @patch("skills.router.skill_svc")
    @patch("skills.router.validate_package")
    @patch("skills.router.import_package_to_catalog")
    async def test_active_record_returns_409(
        self, mock_import, mock_validate, mock_svc, tmp_workspace,
    ):
        """Active existing record → 409 conflict."""
        app = _build_test_app()

        # Setup mocks
        meta = MagicMock()
        meta.name = "pdf-rename"
        meta.version = "1.1.0"
        meta.description = "test"
        meta.body = "body"
        meta.tags = ["test"]
        meta.icon = ""
        meta.license = ""
        meta.compatibility = ""
        meta.metadata = {}
        mock_validate.return_value = MagicMock(valid=True, meta=meta)

        active_record = _make_skill_record("pdf-rename", "1.1.0", is_active=True)
        mock_svc.get_skill_by_name_version = AsyncMock(return_value=active_record)

        admin = _make_admin()
        app.dependency_overrides = {}
        from api.deps import get_current_user, require_admin
        from core.database import get_db
        app.dependency_overrides[require_admin] = lambda: admin
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/skills/import-local",
                json={"source_path": "/some/path"},
            )

        assert resp.status_code == 409
        mock_import.assert_not_called()

    @pytest.mark.asyncio
    @patch("skills.router.skill_svc")
    @patch("skills.router.validate_package")
    @patch("skills.router.import_package_to_catalog")
    async def test_inactive_record_reactivates(
        self, mock_import, mock_validate, mock_svc, tmp_workspace,
    ):
        """Inactive (soft-deleted) record → reactivates, returns 201."""
        app = _build_test_app()

        meta = MagicMock()
        meta.name = "pdf-rename"
        meta.version = "1.1.0"
        meta.description = "test"
        meta.body = "body"
        meta.tags = ["test"]
        meta.icon = ""
        meta.license = ""
        meta.compatibility = ""
        meta.metadata = {}
        mock_validate.return_value = MagicMock(valid=True, meta=meta)

        inactive_record = _make_skill_record("pdf-rename", "1.1.0", is_active=False)
        mock_svc.get_skill_by_name_version = AsyncMock(return_value=inactive_record)

        # update_skill should return a reactivated record
        reactivated = _make_skill_record("pdf-rename", "1.1.0", is_active=True)
        mock_svc.update_skill = AsyncMock(return_value=reactivated)

        admin = _make_admin()
        from api.deps import get_current_user, require_admin
        from core.database import get_db
        app.dependency_overrides[require_admin] = lambda: admin
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/skills/import-local",
                json={"source_path": "/some/path"},
            )

        assert resp.status_code == 201
        mock_import.assert_called_once()
        mock_svc.update_skill.assert_called_once()
        # Verify is_active=True was passed
        call_kwargs = mock_svc.update_skill.call_args
        assert call_kwargs.kwargs.get("is_active") is True

    @pytest.mark.asyncio
    @patch("skills.router.skill_svc")
    @patch("skills.router.validate_package")
    @patch("skills.router.import_package_to_catalog")
    async def test_no_prior_record_creates_new(
        self, mock_import, mock_validate, mock_svc, tmp_workspace,
    ):
        """No prior record → creates new, returns 201."""
        app = _build_test_app()

        meta = MagicMock()
        meta.name = "new-skill"
        meta.version = "1.0.0"
        meta.description = "new"
        meta.body = "body"
        meta.tags = ["test"]
        meta.icon = ""
        meta.license = ""
        meta.compatibility = ""
        meta.metadata = {}
        mock_validate.return_value = MagicMock(valid=True, meta=meta)

        mock_svc.get_skill_by_name_version = AsyncMock(return_value=None)

        new_record = _make_skill_record("new-skill", "1.0.0", is_active=True)
        mock_svc.create_skill = AsyncMock(return_value=new_record)

        admin = _make_admin()
        from api.deps import get_current_user, require_admin
        from core.database import get_db
        app.dependency_overrides[require_admin] = lambda: admin
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/skills/import-local",
                json={"source_path": "/some/path"},
            )

        assert resp.status_code == 201
        mock_import.assert_called_once()
        mock_svc.create_skill.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
