"""ModelConfig request/response schemas (Phase I2).

All response schemas mask api_key — frontend never receives the full key.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


def _mask_api_key(key: str) -> str:
    """Mask an API key for safe display: show first 3 + last 4 chars."""
    if not key or key == "no-key":
        return key
    if len(key) <= 8:
        return key[:2] + "****"
    return key[:3] + "****" + key[-4:]


# ── Request ──────────────────────────────────────────────────────────────────


class ModelConfigCreate(BaseModel):
    name: str = Field(..., max_length=128)
    provider_type: str = Field(default="openai_compatible", max_length=32)
    base_url: str = Field(..., max_length=512)
    api_key: str = Field(default="", max_length=512)
    model_id: str = Field(..., max_length=128)
    is_enabled: bool = True
    is_default: bool = False
    capabilities: Optional[dict[str, Any]] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=600)
    extra_params: Optional[dict[str, Any]] = None


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    provider_type: Optional[str] = Field(None, max_length=32)
    base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, max_length=512)
    model_id: Optional[str] = Field(None, max_length=128)
    is_enabled: Optional[bool] = None
    capabilities: Optional[dict[str, Any]] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=600)
    extra_params: Optional[dict[str, Any]] = None


# ── Response ─────────────────────────────────────────────────────────────────


class ModelConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider_type: str
    base_url: str
    api_key_masked: str = ""
    model_id: str
    is_enabled: bool
    is_default: bool
    capabilities: Optional[dict[str, Any]] = None
    timeout_seconds: Optional[int] = None
    extra_params: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def mask_key(cls, data):
        if hasattr(data, "api_key"):
            raw = data.api_key
        elif isinstance(data, dict):
            raw = data.get("api_key", "")
        else:
            raw = ""
        if hasattr(data, "__dict__"):
            # ORM object — build dict
            d = {
                "id": data.id,
                "name": data.name,
                "provider_type": data.provider_type,
                "base_url": data.base_url,
                "api_key_masked": _mask_api_key(raw),
                "model_id": data.model_id,
                "is_enabled": data.is_enabled,
                "is_default": data.is_default,
                "capabilities": data.capabilities,
                "timeout_seconds": data.timeout_seconds,
                "extra_params": data.extra_params,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
            }
            return d
        # dict input
        data["api_key_masked"] = _mask_api_key(raw)
        return data


class RuntimeModelConfigResponse(BaseModel):
    """Current active model config summary for admin runtime view."""
    source: str  # "db_default" | "env_fallback"
    active_config: dict[str, Any]
    available_configs: list[dict[str, Any]]
