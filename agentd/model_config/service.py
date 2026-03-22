"""ModelConfig service layer (Phase I2).

Provides CRUD, enable/disable, set-default, and the unified resolver.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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
    provider_type: str = "openai_compatible",
    is_enabled: bool = True,
    is_default: bool = False,
    capabilities: dict | None = None,
    timeout_seconds: int | None = None,
    extra_params: dict | None = None,
) -> ModelConfig:
    if is_default:
        await _clear_default(db)

    mc = ModelConfig(
        name=name,
        provider_type=provider_type,
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        is_enabled=is_enabled,
        is_default=is_default and is_enabled,
        capabilities=capabilities,
        timeout_seconds=timeout_seconds,
        extra_params=extra_params,
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

    await _clear_default(db)
    mc.is_default = True
    mc.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return mc


async def _clear_default(db: AsyncSession) -> None:
    await db.execute(
        sa_update(ModelConfig)
        .where(ModelConfig.is_default == True)  # noqa: E712
        .values(is_default=False)
    )


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
    timeout_seconds: int | None = None
    extra_params: dict | None = None


async def resolve_active_model_config(db: AsyncSession) -> ResolvedModelConfig:
    """Resolve the currently active model configuration.

    Priority:
      1. DB row with is_default=True AND is_enabled=True
      2. Environment variable fallback (settings.*)
    """
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.is_default == True,  # noqa: E712
            ModelConfig.is_enabled == True,  # noqa: E712
        )
    )
    mc = result.scalar_one_or_none()

    if mc:
        return ResolvedModelConfig(
            source="db_default",
            name=mc.name,
            base_url=mc.base_url,
            api_key=mc.api_key,
            model_id=mc.model_id,
            config_id=mc.id,
            timeout_seconds=mc.timeout_seconds,
            extra_params=mc.extra_params,
        )

    return ResolvedModelConfig(
        source="env_fallback",
        name="Environment Default",
        base_url=settings.local_llm_url,
        api_key=settings.llm_api_key,
        model_id=settings.default_model_id,
    )
