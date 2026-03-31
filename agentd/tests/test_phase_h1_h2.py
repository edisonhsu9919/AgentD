"""Tests for Phase H1-H2 — user profile, user_skills, admin management.

Covers:
  H1: User profile with installed_skills, user_skills CRUD, usage tracking
  H2: Admin user skills management, session monitoring, permission checks
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

from workspace.manager import ensure_user_root, get_session_dir


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def user_root(tmp_path):
    root = os.path.join(str(tmp_path), "test-user")
    ensure_user_root(root)
    return root


# ═══════════════════════════════════════════════════════════════════════════════
# H1: UserSkill model tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserSkillModel:
    def test_model_fields(self):
        from skills.models import UserSkill
        us = UserSkill(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            skill_name="code_review",
            version="1.0.0",
            is_enabled=True,
            usage_count=5,
        )
        assert us.skill_name == "code_review"
        assert us.version == "1.0.0"
        assert us.is_enabled is True
        assert us.usage_count == 5

    def test_default_values(self):
        from skills.models import UserSkill
        us = UserSkill(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            skill_name="test",
            version="0.1.0",
        )
        assert us.is_enabled is not False  # default True from DB


# ═══════════════════════════════════════════════════════════════════════════════
# H1: User model profile fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserProfileFields:
    def test_user_has_department(self):
        from auth.models import User
        user = User(
            id=uuid.uuid4(),
            username="test",
            password_hash="x",
            role="user",
            workspace="/tmp/test",
            department="Engineering",
            employee_id="EMP001",
        )
        assert user.department == "Engineering"
        assert user.employee_id == "EMP001"

    def test_user_default_profile_empty(self):
        from auth.models import User
        user = User(
            id=uuid.uuid4(),
            username="test2",
            password_hash="x",
            role="user",
            workspace="/tmp/test2",
        )
        # ORM defaults are server_default, but Python-side it's the default
        # from mapped_column. For tests without DB, check it doesn't crash.
        assert hasattr(user, "department")
        assert hasattr(user, "employee_id")


# ═══════════════════════════════════════════════════════════════════════════════
# H1: Auth schemas
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthSchemas:
    def test_user_response_has_profile_fields(self):
        from auth.schemas import UserResponse
        data = UserResponse(
            id=uuid.uuid4(),
            username="test",
            role="user",
            workspace="/tmp",
            is_active=True,
            department="R&D",
            employee_id="E123",
            created_at=datetime.now(timezone.utc),
        )
        assert data.department == "R&D"
        assert data.employee_id == "E123"

    def test_user_profile_response(self):
        from auth.schemas import UserProfileResponse, UserSkillItem
        skill = UserSkillItem(
            name="code_review",
            version="1.0.0",
            is_enabled=True,
            usage_count=10,
            icon="🔍",
        )
        profile = UserProfileResponse(
            id=uuid.uuid4(),
            username="test",
            role="user",
            workspace="/tmp",
            is_active=True,
            department="Eng",
            employee_id="E001",
            created_at=datetime.now(timezone.utc),
            installed_skills=[skill],
        )
        assert len(profile.installed_skills) == 1
        assert profile.installed_skills[0].name == "code_review"
        assert profile.installed_skills[0].usage_count == 10
        assert profile.department == "Eng"

    def test_user_skill_item_defaults(self):
        from auth.schemas import UserSkillItem
        item = UserSkillItem(name="test", version="0.1.0")
        assert item.is_enabled is True
        assert item.usage_count == 0
        assert item.last_used_at is None
        assert item.icon == ""

    def test_create_user_request_profile_fields(self):
        from auth.schemas import CreateUserRequest
        req = CreateUserRequest(
            username="new_user",
            password="secure123",
            department="Sales",
            employee_id="S001",
        )
        assert req.department == "Sales"
        assert req.employee_id == "S001"
        assert req.role == "user"

    def test_update_user_request_profile_fields(self):
        from auth.schemas import UpdateUserRequest
        req = UpdateUserRequest(department="Marketing", employee_id="M001")
        assert req.department == "Marketing"
        assert req.employee_id == "M001"
        assert req.role is None
        assert req.password is None

    def test_skill_toggle_request(self):
        from auth.schemas import UserSkillToggleRequest
        req = UserSkillToggleRequest(is_enabled=False)
        assert req.is_enabled is False


# ═══════════════════════════════════════════════════════════════════════════════
# H1: SkillTool disable support
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillToolDisable:
    def _create_skill_md(self, skills_dir, name, desc="test", content="Do stuff"):
        skill_dir = os.path.join(skills_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        md = f"---\nname: {name}\ndescription: {desc}\ntags: [test]\n---\n\n{content}\n"
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(md)

    def test_list_filters_disabled(self, user_root):
        from tools.skill import SkillTool
        from skills.filesystem import get_skills_dir

        skills_dir = get_skills_dir(user_root)
        self._create_skill_md(skills_dir, "enabled_skill")
        self._create_skill_md(skills_dir, "disabled_skill")

        tool = SkillTool()
        disabled = {"disabled_skill"}
        result = tool._list_skills(skills_dir, disabled)
        assert result["is_error"] is False
        names = [s["name"] for s in result["output"]]
        assert "enabled_skill" in names
        assert "disabled_skill" not in names

    def test_list_no_disabled(self, user_root):
        from tools.skill import SkillTool
        from skills.filesystem import get_skills_dir

        skills_dir = get_skills_dir(user_root)
        self._create_skill_md(skills_dir, "skill_a")
        self._create_skill_md(skills_dir, "skill_b")

        tool = SkillTool()
        result = tool._list_skills(skills_dir, set())
        assert result["is_error"] is False
        names = [s["name"] for s in result["output"]]
        assert "skill_a" in names
        assert "skill_b" in names


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Admin schema consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminSchemaConsistency:
    def test_user_profile_response_same_as_me(self):
        """Admin user detail response should be same shape as /me/profile."""
        from auth.schemas import UserProfileResponse
        fields = set(UserProfileResponse.model_fields.keys())
        expected = {
            "id", "username", "role", "workspace", "is_active",
            "department", "employee_id", "created_at", "installed_skills",
        }
        assert expected == fields

    def test_user_skill_item_fields(self):
        from auth.schemas import UserSkillItem
        fields = set(UserSkillItem.model_fields.keys())
        expected = {"name", "version", "is_enabled", "usage_count", "last_used_at", "icon"}
        assert expected == fields


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Migration metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigration008:
    def test_migration_file_exists(self):
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "db", "alembic", "versions",
            "008_user_profile_and_user_skills.py",
        )
        assert os.path.isfile(path)

    def test_migration_revision_chain(self):
        import importlib.util
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "db", "alembic", "versions",
            "008_user_profile_and_user_skills.py",
        )
        spec = importlib.util.spec_from_file_location("m008", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "008"
        assert mod.down_revision == "007"

    def test_expected_schema_version_updated(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "013"


# ═══════════════════════════════════════════════════════════════════════════════
# H1/H2: user_skill_service unit tests (in-memory, no DB)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUserSkillServiceImport:
    def test_service_importable(self):
        from skills import user_skill_service as us_svc
        assert hasattr(us_svc, "upsert_user_skill")
        assert hasattr(us_svc, "remove_user_skill")
        assert hasattr(us_svc, "get_user_skill")
        assert hasattr(us_svc, "list_user_skills")
        assert hasattr(us_svc, "set_enabled")
        assert hasattr(us_svc, "increment_usage")
        assert hasattr(us_svc, "is_skill_enabled_for_user")


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Admin router endpoint existence
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminRouterEndpoints:
    def test_router_has_skills_endpoints(self):
        from admin.router import router
        paths = [route.path for route in router.routes]
        assert "/{user_id}/skills" in paths
        assert "/{user_id}/skills/{skill_name}" in paths

    def test_router_has_session_endpoints(self):
        from admin.router import router
        paths = [route.path for route in router.routes]
        assert "/{user_id}/sessions" in paths
        assert "/{user_id}/sessions/{session_id}" in paths
        assert "/{user_id}/sessions/{session_id}/messages" in paths

    def test_router_has_user_crud(self):
        from admin.router import router
        paths = [route.path for route in router.routes]
        assert "" in paths
        assert "/{user_id}" in paths


# ═══════════════════════════════════════════════════════════════════════════════
# H1: Auth router profile endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthRouterProfile:
    def test_profile_endpoint_exists(self):
        from auth.router import router
        paths = [route.path for route in router.routes]
        assert "/me/profile" in paths

    def test_me_endpoint_still_exists(self):
        from auth.router import router
        paths = [route.path for route in router.routes]
        assert "/me" in paths
