"""ModelConfig service layer (Phase I2 + O3-1 VLM support).

Provides CRUD, enable/disable, set-default, and the unified resolver.
Phase O3-1: is_default is type-scoped (one default LLM + one default VLM).
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from model_config.models import ModelConfig


# ── CRUD ────────────────────────────────────────────────────────────────────


async def list_model_configs(db: AsyncSession) -> list[ModelConfig]:
    result = await db.execute(
        select(ModelConfig).order_by(ModelConfig.is_default.desc(), ModelConfig.name)
    )
    return list(result.scalars().all())


async def get_model_config(db: AsyncSession, config_id: uuid.UUID) -> Optional[ModelConfig]:
    return await db.get(ModelConfig, config_id)


async def get_model_config_by_name(db: AsyncSession, name: str) -> Optional[ModelConfig]:
    result = await db.execute(select(ModelConfig).where(ModelConfig.name == name))
    return result.scalar_one_or_none()


async def create_model_config(
    db: AsyncSession,
    *,
    name: str,
    base_url: str,
    model_id: str,
    api_key: str = "",
    model_type: str = "llm",
    provider_type: str = "openai_compatible",
    is_enabled: bool = True,
    is_default: bool = False,
    capabilities: dict | None = None,
    timeout_seconds: int | None = None,
    extra_params: dict | None = None,
    context_window: int | None = None,
) -> ModelConfig:
    if is_default:
        await _clear_default(db, model_type=model_type)

    mc = ModelConfig(
        name=name,
        model_type=model_type,
        provider_type=provider_type,
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        is_enabled=is_enabled,
        is_default=is_default and is_enabled,
        capabilities=capabilities,
        timeout_seconds=timeout_seconds,
        extra_params=extra_params,
        context_window=context_window,
    )
    db.add(mc)
    await db.flush()
    return mc


async def update_model_config(
    db: AsyncSession,
    config_id: uuid.UUID,
    **kwargs,
) -> Optional[ModelConfig]:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return None

    for key, value in kwargs.items():
        if value is not None and hasattr(mc, key):
            setattr(mc, key, value)

    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def delete_model_config(db: AsyncSession, config_id: uuid.UUID) -> bool:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return False
    await db.delete(mc)
    await db.flush()
    return True


# ── Enable / Disable / Set Default ─────────────────────────────────────────


async def enable_model_config(db: AsyncSession, config_id: uuid.UUID) -> Optional[ModelConfig]:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return None
    mc.is_enabled = True
    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def disable_model_config(db: AsyncSession, config_id: uuid.UUID) -> Optional[ModelConfig]:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return None
    mc.is_enabled = False
    if mc.is_default:
        mc.is_default = False
    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def set_default_model_config(db: AsyncSession, config_id: uuid.UUID) -> Optional[ModelConfig]:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return None
    if not mc.is_enabled:
        return None  # Cannot set disabled config as default

    await _clear_default(db, model_type=mc.model_type)
    mc.is_default = True
    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def unset_default_model_config(db: AsyncSession, config_id: uuid.UUID) -> Optional[ModelConfig]:
    mc = await db.get(ModelConfig, config_id)
    if not mc:
        return None
    mc.is_default = False
    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def _clear_default(db: AsyncSession, model_type: str = "llm") -> None:
    """Clear is_default for configs of the given model_type only."""
    await db.execute(
        sa_update(ModelConfig)
        .where(
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.model_type == model_type,
        )
        .values(is_default=False)
    )


# ── Provider discovery (cached) ────────────────────────────────────────────

# In-memory cache: key = (base_url, model_id) → (value, timestamp)
_provider_cw_cache: dict[tuple[str, str], tuple[int | None, float]] = {}
_PROVIDER_CW_TTL = 300  # 5 minutes


def invalidate_provider_cache(base_url: str | None = None, model_id: str | None = None) -> None:
    """Invalidate provider context_window cache.

    Called when model config is updated/created so the next resolve
    picks up fresh provider metadata. Pass both args to clear a specific
    entry, or neither to clear all.
    """
    if base_url and model_id:
        _provider_cw_cache.pop((base_url, model_id), None)
    else:
        _provider_cw_cache.clear()


async def discover_provider_context_window(
    base_url: str, model_id: str, api_key: str = "",
) -> int | None:
    """Discover context_window from the provider's /models endpoint.

    Queries the OpenAI-compatible /models endpoint and extracts the context
    window size. Checks multiple locations because providers differ:
      - Top-level: context_length, max_model_len, context_window (vLLM, LM Studio)
      - Nested: meta.n_ctx_train (llama.cpp / llama-server)

    Results are cached in-memory (TTL 5 min) to avoid hitting /models on
    every agent run. Cache is invalidated on model config updates.
    Returns None if discovery fails — caller should fall back to manual config.
    """
    cache_key = (base_url, model_id)
    cached = _provider_cw_cache.get(cache_key)
    if cached is not None:
        value, ts = cached
        if time.monotonic() - ts < _PROVIDER_CW_TTL:
            return value

    result = await _fetch_provider_context_window(base_url, model_id, api_key)
    _provider_cw_cache[cache_key] = (result, time.monotonic())
    return result


async def _fetch_provider_context_window(
    base_url: str, model_id: str, api_key: str,
) -> int | None:
    """Actual HTTP call to provider /models — called by discover_provider_context_window."""
    headers: dict[str, str] = {}
    if api_key and api_key != "no-key":
        headers["Authorization"] = f"Bearer {api_key}"

    url = base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3.0) as client:
            resp = await client.get(f"{url}/models", headers=headers)
            if resp.status_code != 200:
                return None
            body = resp.json()
            models = body.get("data", [])
            if isinstance(body, list):
                models = body
            for m in models:
                mid = m.get("id", "")
                if mid == model_id or model_id in mid or mid in model_id:
                    # 1. Top-level fields (vLLM, LM Studio, etc.)
                    for key in ("context_length", "max_model_len", "context_window"):
                        val = m.get(key)
                        if val and isinstance(val, (int, float)):
                            return int(val)
                    # 2. Nested meta (llama.cpp / llama-server)
                    meta = m.get("meta")
                    if isinstance(meta, dict):
                        for key in ("n_ctx_train", "context_length"):
                            val = meta.get(key)
                            if val and isinstance(val, (int, float)):
                                return int(val)
    except Exception:
        pass

    return None


# ── Resolver ────────────────────────────────────────────────────────────────


@dataclass
class ResolvedModelConfig:
    """Unified model config returned by the resolver."""
    source: str  # "db_default" | "env_fallback"
    name: str
    base_url: str
    api_key: str
    model_id: str
    config_id: uuid.UUID | None = None
    capabilities: dict | None = None
    timeout_seconds: int | None = None
    extra_params: dict | None = None
    context_window: int | None = None


async def resolve_active_model_config(db: AsyncSession) -> ResolvedModelConfig:
    """Resolve the currently active LLM configuration.

    Priority:
      1. DB row with model_type='llm', is_default=True, is_enabled=True
      2. Environment variable fallback (settings.*)

    context_window priority:
      1. Provider /models API discovery (live truth)
      2. DB model_configs.context_window (manual override)
      3. settings.context_window_tokens (env fallback)
    """
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.model_type == "llm",
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.is_enabled == True,  # noqa: E712
        )
    )
    mc = result.scalar_one_or_none()

    if mc:
        # Manual fallback: DB config > env setting
        manual_cw = mc.context_window or settings.context_window_tokens
        # Try provider discovery first (best-effort)
        discovered_cw = await discover_provider_context_window(
            mc.base_url, mc.model_id, mc.api_key,
        )
        return ResolvedModelConfig(
            source="db_default",
            name=mc.name,
            base_url=mc.base_url,
            api_key=mc.api_key,
            model_id=mc.model_id,
            config_id=mc.id,
            capabilities=mc.capabilities,
            timeout_seconds=mc.timeout_seconds,
            extra_params=mc.extra_params,
            context_window=discovered_cw or manual_cw,
        )

    # Env fallback path
    manual_cw = settings.context_window_tokens
    discovered_cw = await discover_provider_context_window(
        settings.local_llm_url, settings.default_model_id, settings.llm_api_key,
    )
    return ResolvedModelConfig(
        source="env_fallback",
        name="Environment Default",
        base_url=settings.local_llm_url,
        api_key=settings.llm_api_key,
        model_id=settings.default_model_id,
        context_window=discovered_cw or manual_cw,
    )


# ── VLM Resolver (Phase O3-1) ─────────────────────────────────────────────


@dataclass
class ResolvedVLMConfig:
    """Unified VLM config returned by the resolver.

    None means no VLM is available — callers should degrade gracefully.
    """
    source: str  # "db_default" | "env_fallback"
    name: str
    base_url: str
    api_key: str
    model_id: str
    config_id: uuid.UUID | None = None
    timeout_seconds: int | None = None
    extra_params: dict | None = None
    # Capability flags
    supports_vision: bool = True
    supports_http_image_url: bool = True
    supports_data_uri_image: bool = True


async def resolve_active_vlm_config(db: AsyncSession) -> ResolvedVLMConfig | None:
    """Resolve the currently active VLM configuration.

    Priority:
      1. DB row with model_type='vlm', is_default=True, is_enabled=True
      2. Environment variable fallback (local_vlm_url + vlm_api_key + default_vlm_id)
      3. None — no VLM available

    Returns None when no VLM is configured. Callers must handle this as
    graceful degradation (not an error).
    """
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.model_type == "vlm",
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.is_enabled == True,  # noqa: E712
        )
    )
    mc = result.scalar_one_or_none()

    if mc:
        caps = mc.capabilities or {}
        return ResolvedVLMConfig(
            source="db_default",
            name=mc.name,
            base_url=mc.base_url,
            api_key=mc.api_key,
            model_id=mc.model_id,
            config_id=mc.id,
            timeout_seconds=mc.timeout_seconds,
            extra_params=mc.extra_params,
            supports_vision=caps.get("supports_vision", True),
            supports_http_image_url=caps.get("supports_http_image_url", True),
            supports_data_uri_image=caps.get("supports_data_uri_image", True),
        )

    # Env fallback — only if VLM URL is configured
    if not settings.local_vlm_url or not settings.default_vlm_id:
        return None

    return ResolvedVLMConfig(
        source="env_fallback",
        name="Environment VLM Default",
        base_url=settings.local_vlm_url,
        api_key=settings.vlm_api_key,
        model_id=settings.default_vlm_id,
    )
