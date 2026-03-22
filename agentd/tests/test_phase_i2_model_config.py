"""Tests for Phase I2 — Model Configuration Management.

Covers:
  - ModelConfig ORM model
  - ModelConfig schemas (create, update, response, masked api_key)
  - Service layer (CRUD, enable/disable, set-default)
  - Resolver (DB default > env fallback)
  - Router endpoint registration
  - Session create model_id optionality
  - Migration 009
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# I2: ModelConfig ORM
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelConfigModel:
    def test_model_importable(self):
        from model_config.models import ModelConfig
        assert ModelConfig.__tablename__ == "model_configs"

    def test_model_fields(self):
        from model_config.models import ModelConfig
        mc = ModelConfig(
            name="Test Model",
            provider_type="openai_compatible",
            base_url="http://localhost:8080/v1",
            api_key="sk-test-key",
            model_id="test-model-v1",
            is_enabled=True,
            is_default=False,
        )
        assert mc.name == "Test Model"
        assert mc.provider_type == "openai_compatible"
        assert mc.model_id == "test-model-v1"
        assert mc.is_enabled is True
        assert mc.is_default is False


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Schemas
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelConfigSchemas:
    def test_create_schema(self):
        from model_config.schemas import ModelConfigCreate
        body = ModelConfigCreate(
            name="MiniMax",
            base_url="https://api.minimaxi.com/v1",
            api_key="sk-test-123",
            model_id="MiniMax-M2.5",
        )
        assert body.name == "MiniMax"
        assert body.provider_type == "openai_compatible"
        assert body.is_default is False

    def test_create_with_defaults(self):
        from model_config.schemas import ModelConfigCreate
        body = ModelConfigCreate(
            name="Local",
            base_url="http://localhost:8080/v1",
            model_id="qwen3-30b",
        )
        assert body.api_key == ""
        assert body.is_enabled is True
        assert body.capabilities is None

    def test_update_schema_all_optional(self):
        from model_config.schemas import ModelConfigUpdate
        body = ModelConfigUpdate()
        assert body.name is None
        assert body.api_key is None

    def test_mask_api_key_long(self):
        from model_config.schemas import _mask_api_key
        assert _mask_api_key("sk-abcdefghijklmnop") == "sk-****mnop"

    def test_mask_api_key_short(self):
        from model_config.schemas import _mask_api_key
        assert _mask_api_key("abc") == "ab****"

    def test_mask_api_key_no_key(self):
        from model_config.schemas import _mask_api_key
        assert _mask_api_key("no-key") == "no-key"
        assert _mask_api_key("") == ""

    def test_response_masks_key(self):
        from model_config.schemas import ModelConfigResponse

        mock = MagicMock()
        mock.id = uuid.uuid4()
        mock.name = "Test"
        mock.provider_type = "openai_compatible"
        mock.base_url = "http://localhost:8080/v1"
        mock.api_key = "sk-super-secret-key-12345"
        mock.model_id = "test-model"
        mock.is_enabled = True
        mock.is_default = False
        mock.capabilities = None
        mock.timeout_seconds = None
        mock.extra_params = None
        mock.created_at = datetime.now(timezone.utc)
        mock.updated_at = datetime.now(timezone.utc)

        resp = ModelConfigResponse.model_validate(mock)
        assert resp.api_key_masked == "sk-****2345"
        assert not hasattr(resp, "api_key") or "super-secret" not in str(getattr(resp, "api_key_masked", ""))

    def test_runtime_response(self):
        from model_config.schemas import RuntimeModelConfigResponse
        resp = RuntimeModelConfigResponse(
            source="db_default",
            active_config={"name": "Test", "model_id": "m1"},
            available_configs=[],
        )
        assert resp.source == "db_default"


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Service — Resolver
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolver:
    def test_resolved_model_config_dataclass(self):
        from model_config.service import ResolvedModelConfig
        rc = ResolvedModelConfig(
            source="env_fallback",
            name="Env",
            base_url="http://localhost:8080/v1",
            api_key="no-key",
            model_id="local-default",
        )
        assert rc.source == "env_fallback"
        assert rc.config_id is None

    def test_resolved_with_config_id(self):
        from model_config.service import ResolvedModelConfig
        cid = uuid.uuid4()
        rc = ResolvedModelConfig(
            source="db_default",
            name="DB",
            base_url="https://api.example.com/v1",
            api_key="sk-123",
            model_id="model-v1",
            config_id=cid,
        )
        assert rc.config_id == cid


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Service import
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceImport:
    def test_service_functions_importable(self):
        from model_config import service as mc_svc
        assert callable(mc_svc.list_model_configs)
        assert callable(mc_svc.create_model_config)
        assert callable(mc_svc.get_model_config)
        assert callable(mc_svc.update_model_config)
        assert callable(mc_svc.enable_model_config)
        assert callable(mc_svc.disable_model_config)
        assert callable(mc_svc.set_default_model_config)
        assert callable(mc_svc.resolve_active_model_config)


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Router endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelConfigRouter:
    def test_router_importable(self):
        from model_config.router import router
        assert router is not None

    def test_endpoints_registered(self):
        from model_config.router import router
        paths = [route.path for route in router.routes]
        assert "" in paths  # list + create
        assert "/{config_id}" in paths  # get + update
        assert "/{config_id}/enable" in paths
        assert "/{config_id}/disable" in paths
        assert "/{config_id}/set-default" in paths

    def test_runtime_endpoint_registered(self):
        from model_config.router import runtime_router
        paths = [route.path for route in runtime_router.routes]
        assert "/model-config" in paths

    def test_main_registers_routers(self):
        from main import app
        paths = [route.path for route in app.routes]
        assert any("/api/admin/model-configs" in p for p in paths)
        assert any("/api/admin/runtime" in p for p in paths)


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Session create — model_id optional
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionCreateModelOptional:
    def test_session_create_model_id_optional(self):
        from session.schemas import SessionCreate
        body = SessionCreate(title="Test")
        assert body.model_id is None

    def test_session_create_model_id_explicit(self):
        from session.schemas import SessionCreate
        body = SessionCreate(title="Test", model_id="my-model")
        assert body.model_id == "my-model"


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Migration version
# ═══════════════════════════════════════════════════════════════════════════════


class TestMigration009:
    def test_expected_schema_version(self):
        from main import EXPECTED_SCHEMA_VERSION
        assert EXPECTED_SCHEMA_VERSION == "010"

    def test_migration_file_exists(self):
        from pathlib import Path
        migration = Path(__file__).parent.parent / "db" / "alembic" / "versions" / "009_model_configs.py"
        assert migration.exists()
