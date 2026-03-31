"""Phase O3-1 — VLM Routing & Config tests.

Tests cover:
- model_config/service.py: type-scoped defaults, ResolvedVLMConfig, resolve_active_vlm_config
- model_config/schemas.py: model_type in create/response
- vlm/provider.py: image encoding, describe_image (mocked), degradation
- core/config.py: VLM settings fields
"""

import base64
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Test: VLM Settings ──────────────────────────────────────────────────────


class TestVLMSettings:

    def test_default_vlm_fields_empty(self):
        """VLM fields default to empty (no VLM configured)."""
        from core.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/test",
            _env_file=None,
        )
        assert s.local_vlm_url == ""
        assert s.vlm_api_key == ""
        assert s.default_vlm_id == ""

    def test_vlm_fields_can_be_set(self):
        from core.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/test",
            local_vlm_url="https://api.example.com/v1",
            vlm_api_key="sk-test",
            default_vlm_id="qwen-vl",
            _env_file=None,
        )
        assert s.local_vlm_url == "https://api.example.com/v1"
        assert s.vlm_api_key == "sk-test"
        assert s.default_vlm_id == "qwen-vl"


# ── Test: Schema model_type ─────────────────────────────────────────────────


class TestSchemaModelType:

    def test_create_schema_default_llm(self):
        from model_config.schemas import ModelConfigCreate

        body = ModelConfigCreate(name="test", base_url="http://x", model_id="m")
        assert body.model_type == "llm"

    def test_create_schema_vlm(self):
        from model_config.schemas import ModelConfigCreate

        body = ModelConfigCreate(
            name="test-vlm", model_type="vlm",
            base_url="http://x", model_id="qwen-vl",
        )
        assert body.model_type == "vlm"

    def test_response_schema_includes_model_type(self):
        from model_config.schemas import ModelConfigResponse

        now = datetime.now(timezone.utc)
        data = {
            "id": uuid.uuid4(),
            "name": "test",
            "model_type": "vlm",
            "provider_type": "openai_compatible",
            "base_url": "http://x",
            "api_key": "no-key",
            "model_id": "m",
            "is_enabled": True,
            "is_default": False,
            "created_at": now,
            "updated_at": now,
        }
        resp = ModelConfigResponse.model_validate(data)
        assert resp.model_type == "vlm"


# ── Test: Type-scoped defaults ───────────────────────────────────────────────


class TestTypeScopedDefaults:

    @pytest.mark.asyncio
    async def test_clear_default_is_type_scoped(self):
        """_clear_default should only clear defaults within the same model_type."""
        from model_config.service import _clear_default
        from model_config.models import ModelConfig
        from sqlalchemy import select

        # Use mock DB session
        mock_db = AsyncMock()
        await _clear_default(mock_db, model_type="vlm")

        # Verify the execute call includes model_type filter
        call_args = mock_db.execute.call_args
        stmt = call_args[0][0]
        # The statement should be an UPDATE with WHERE conditions
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql = str(compiled)
        assert "vlm" in sql
        assert "model_type" in sql


# ── Test: ResolvedVLMConfig ──────────────────────────────────────────────────


class TestResolvedVLMConfig:

    def test_dataclass_defaults(self):
        from model_config.service import ResolvedVLMConfig

        cfg = ResolvedVLMConfig(
            source="env_fallback",
            name="test",
            base_url="http://x",
            api_key="sk",
            model_id="m",
        )
        assert cfg.supports_vision is True
        assert cfg.supports_http_image_url is True
        assert cfg.supports_data_uri_image is True
        assert cfg.config_id is None

    def test_dataclass_custom_caps(self):
        from model_config.service import ResolvedVLMConfig

        cfg = ResolvedVLMConfig(
            source="db_default",
            name="test",
            base_url="http://x",
            api_key="sk",
            model_id="m",
            supports_data_uri_image=False,
        )
        assert cfg.supports_data_uri_image is False


# ── Test: resolve_active_vlm_config ─────────────────────────────────────────


class TestResolveVLMConfig:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_vlm_configured(self):
        """No DB default and no env VLM → returns None."""
        from model_config.service import resolve_active_vlm_config

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch("model_config.service.settings") as mock_settings:
            mock_settings.local_vlm_url = ""
            mock_settings.vlm_api_key = ""
            mock_settings.default_vlm_id = ""

            result = await resolve_active_vlm_config(mock_db)
            assert result is None

    @pytest.mark.asyncio
    async def test_env_fallback_when_configured(self):
        """No DB default but env VLM vars set → returns env fallback."""
        from model_config.service import resolve_active_vlm_config

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch("model_config.service.settings") as mock_settings:
            mock_settings.local_vlm_url = "https://api.example.com/v1"
            mock_settings.vlm_api_key = "sk-test"
            mock_settings.default_vlm_id = "qwen-vl"

            result = await resolve_active_vlm_config(mock_db)
            assert result is not None
            assert result.source == "env_fallback"
            assert result.model_id == "qwen-vl"
            assert result.base_url == "https://api.example.com/v1"

    @pytest.mark.asyncio
    async def test_db_default_takes_priority(self):
        """DB row with model_type=vlm, is_default=True → used over env."""
        from model_config.service import resolve_active_vlm_config

        mock_mc = MagicMock()
        mock_mc.name = "Qwen VL"
        mock_mc.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        mock_mc.api_key = "sk-db"
        mock_mc.model_id = "qwen3-vl-flash"
        mock_mc.id = uuid.uuid4()
        mock_mc.timeout_seconds = 30
        mock_mc.extra_params = None
        mock_mc.capabilities = {"supports_vision": True, "supports_data_uri_image": True}

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_mc
        mock_db.execute.return_value = mock_result

        result = await resolve_active_vlm_config(mock_db)
        assert result is not None
        assert result.source == "db_default"
        assert result.model_id == "qwen3-vl-flash"
        assert result.config_id == mock_mc.id

    @pytest.mark.asyncio
    async def test_db_capabilities_propagate(self):
        """capabilities JSONB flags should map to VLM config fields."""
        from model_config.service import resolve_active_vlm_config

        mock_mc = MagicMock()
        mock_mc.name = "Local VLM"
        mock_mc.base_url = "http://localhost:8080/v1"
        mock_mc.api_key = "no-key"
        mock_mc.model_id = "local-vlm"
        mock_mc.id = uuid.uuid4()
        mock_mc.timeout_seconds = None
        mock_mc.extra_params = None
        mock_mc.capabilities = {
            "supports_vision": True,
            "supports_http_image_url": False,
            "supports_data_uri_image": True,
        }

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_mc
        mock_db.execute.return_value = mock_result

        result = await resolve_active_vlm_config(mock_db)
        assert result.supports_http_image_url is False
        assert result.supports_data_uri_image is True


# ── Test: VLM Provider — image encoding ─────────────────────────────────────


class TestImageEncoding:

    def test_encode_png(self, tmp_path):
        from vlm.provider import encode_image_to_data_uri

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        uri = encode_image_to_data_uri(str(img))
        assert uri.startswith("data:image/png;base64,")
        # Verify roundtrip
        encoded_part = uri.split(",", 1)[1]
        decoded = base64.b64decode(encoded_part)
        assert decoded[:4] == b"\x89PNG"

    def test_encode_jpeg(self, tmp_path):
        from vlm.provider import encode_image_to_data_uri

        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        uri = encode_image_to_data_uri(str(img))
        assert uri.startswith("data:image/jpeg;base64,")

    def test_encode_file_not_found(self):
        from vlm.provider import encode_image_to_data_uri

        with pytest.raises(FileNotFoundError):
            encode_image_to_data_uri("/tmp/nonexistent_image.png")

    def test_encode_unsupported_type(self, tmp_path):
        from vlm.provider import encode_image_to_data_uri

        f = tmp_path / "test.txt"
        f.write_text("not an image")
        with pytest.raises(ValueError, match="Unsupported"):
            encode_image_to_data_uri(str(f))

    def test_build_image_url_http(self):
        from vlm.provider import build_image_url

        url = "https://example.com/image.png"
        assert build_image_url(url) == url

    def test_build_image_url_data_uri(self):
        from vlm.provider import build_image_url

        uri = "data:image/png;base64,abc123"
        assert build_image_url(uri) == uri

    def test_build_image_url_local_file(self, tmp_path):
        from vlm.provider import build_image_url

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        result = build_image_url(str(img))
        assert result.startswith("data:image/png;base64,")


# ── Test: VLM Provider — describe_image (mocked) ───────────────────────────


class TestDescribeImage:

    @pytest.mark.asyncio
    async def test_success(self):
        from vlm.provider import describe_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "A cat sitting on a table."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await describe_image(
                image_source="https://example.com/cat.png",
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_id="qwen-vl",
            )

        assert result.success is True
        assert "cat" in result.content.lower()
        assert result.usage is not None

    @pytest.mark.asyncio
    async def test_api_error(self):
        from vlm.provider import describe_image

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await describe_image(
                image_source="https://example.com/cat.png",
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_id="qwen-vl",
            )

        assert result.success is False
        assert "429" in result.content

    @pytest.mark.asyncio
    async def test_timeout(self):
        import httpx as _httpx
        from vlm.provider import describe_image

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = _httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await describe_image(
                image_source="https://example.com/cat.png",
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_id="qwen-vl",
            )

        assert result.success is False
        assert "timed out" in result.content.lower()

    @pytest.mark.asyncio
    async def test_invalid_image_source(self):
        from vlm.provider import describe_image

        result = await describe_image(
            image_source="/tmp/nonexistent.png",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model_id="qwen-vl",
        )
        assert result.success is False
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_empty_choices(self):
        from vlm.provider import describe_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await describe_image(
                image_source="https://example.com/cat.png",
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                model_id="qwen-vl",
            )

        assert result.success is False
        assert "empty" in result.content.lower()


# ── Test: check_vlm_available ───────────────────────────────────────────────


class TestCheckVLMAvailable:

    @pytest.mark.asyncio
    async def test_available(self):
        from vlm.provider import check_vlm_available

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await check_vlm_available("https://api.example.com/v1", "sk-test")
        assert result is True

    @pytest.mark.asyncio
    async def test_unavailable(self):
        from vlm.provider import check_vlm_available

        with patch("vlm.provider.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await check_vlm_available("http://localhost:9999/v1")
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
