"""Admin model configuration router (Phase I2).

All endpoints require admin privileges.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, require_admin
from auth.models import User
from core.database import get_db
from core.response import ok, ok_list
from model_config import service as mc_svc
from model_config.schemas import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    RuntimeModelConfigResponse,
    _mask_api_key,
)
from model_config.service import invalidate_provider_cache

router = APIRouter()
runtime_router = APIRouter()  # Mounted separately at /api/admin/runtime
public_runtime_router = APIRouter()  # Mounted separately at /api/runtime


@router.get("")
async def list_model_configs(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    configs = await mc_svc.list_model_configs(db)
    data = [ModelConfigResponse.model_validate(c).model_dump(mode="json") for c in configs]
    return ok_list(data, total=len(data))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_model_config(
    body: ModelConfigCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await mc_svc.get_model_config_by_name(db, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": f"Model config '{body.name}' already exists"},
        )
    mc = await mc_svc.create_model_config(
        db,
        name=body.name,
        model_type=body.model_type,
        provider_type=body.provider_type,
        base_url=body.base_url,
        api_key=body.api_key,
        model_id=body.model_id,
        is_enabled=body.is_enabled,
        is_default=body.is_default,
        capabilities=body.capabilities,
        timeout_seconds=body.timeout_seconds,
        context_window=body.context_window,
        extra_params=body.extra_params,
    )
    await db.commit()
    invalidate_provider_cache()
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


@router.get("/{config_id}")
async def get_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.get_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


@router.patch("/{config_id}")
async def update_model_config(
    config_id: uuid.UUID,
    body: ModelConfigUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.get_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    # Check name uniqueness if changing
    if body.name and body.name != mc.name:
        existing = await mc_svc.get_model_config_by_name(db, body.name)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CONFLICT", "message": f"Model config '{body.name}' already exists"},
            )

    update_data = body.model_dump(exclude_none=True)
    updated = await mc_svc.update_model_config(db, config_id, **update_data)
    await db.commit()
    invalidate_provider_cache()
    return ok(ModelConfigResponse.model_validate(updated).model_dump(mode="json"))


@router.delete("/{config_id}")
async def delete_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    deleted = await mc_svc.delete_model_config(db, config_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    await db.commit()
    invalidate_provider_cache()
    return ok({"deleted": True, "id": str(config_id)})


@router.post("/{config_id}/enable")
async def enable_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.enable_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    await db.commit()
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


@router.post("/{config_id}/disable")
async def disable_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.disable_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    await db.commit()
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


@router.post("/{config_id}/set-default")
async def set_default_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.set_default_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "message": "Model config not found or not enabled (must be enabled to set as default)",
            },
        )
    await db.commit()
    invalidate_provider_cache()
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


@router.post("/{config_id}/unset-default")
async def unset_default_model_config(
    config_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mc = await mc_svc.unset_default_model_config(db, config_id)
    if not mc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Model config not found"},
        )
    await db.commit()
    invalidate_provider_cache()
    return ok(ModelConfigResponse.model_validate(mc).model_dump(mode="json"))


# ── Runtime summary ──────────────────────────────────────────────────────────


@runtime_router.get("/model-config")
async def get_runtime_model_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the currently active model config and all available configs."""
    return ok(await _build_runtime_model_config_payload(db, include_available=True))


@public_runtime_router.get("/model-config")
async def get_public_runtime_model_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the active LLM summary for authenticated workspace users."""
    return ok(await _build_runtime_model_config_payload(db, include_available=False))


async def _build_runtime_model_config_payload(
    db: AsyncSession,
    *,
    include_available: bool,
) -> dict:
    resolved = await mc_svc.resolve_active_model_config(db)

    active = {
        "source": resolved.source,
        "name": resolved.name,
        "base_url": resolved.base_url,
        "api_key_masked": _mask_api_key(resolved.api_key),
        "model_id": resolved.model_id,
        "context_window": resolved.context_window,
    }
    if resolved.config_id:
        active["config_id"] = str(resolved.config_id)

    available = []
    if include_available:
        configs = await mc_svc.list_model_configs(db)
        available = [
            {
                "id": str(c.id),
                "name": c.name,
                "model_id": c.model_id,
                "is_enabled": c.is_enabled,
                "is_default": c.is_default,
            }
            for c in configs
            if getattr(c, "model_type", "llm") == "llm"
        ]

    return RuntimeModelConfigResponse(
        source=resolved.source,
        active_config=active,
        available_configs=available,
    ).model_dump(mode="json")


@runtime_router.get("/vlm-config")
async def get_runtime_vlm_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the currently active VLM config (or null if none)."""
    return ok(await _build_runtime_vlm_config_payload(db))


@public_runtime_router.get("/vlm-config")
async def get_public_runtime_vlm_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the active VLM summary for authenticated workspace users."""
    return ok(await _build_runtime_vlm_config_payload(db))


async def _build_runtime_vlm_config_payload(db: AsyncSession) -> dict:
    resolved = await mc_svc.resolve_active_vlm_config(db)

    if resolved is None:
        return {
            "available": False,
            "source": None,
            "active_config": None,
            "message": "No VLM configured. Vision capabilities are unavailable.",
        }

    active = {
        "source": resolved.source,
        "name": resolved.name,
        "base_url": resolved.base_url,
        "api_key_masked": _mask_api_key(resolved.api_key),
        "model_id": resolved.model_id,
        "supports_vision": resolved.supports_vision,
        "supports_http_image_url": resolved.supports_http_image_url,
        "supports_data_uri_image": resolved.supports_data_uri_image,
    }
    if resolved.config_id:
        active["config_id"] = str(resolved.config_id)

    return {
        "available": True,
        "source": resolved.source,
        "active_config": active,
    }


@runtime_router.get("/diagnostics")
async def get_runtime_diagnostics(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only runtime diagnostics for troubleshooting.

    Returns comprehensive instance, schema, model, and config information.
    """
    import os as _os
    from sqlalchemy import text

    from main import _INSTANCE_ID, _STARTED_AT, EXPECTED_SCHEMA_VERSION
    from core.config import settings

    # ── Schema ────────────────────────────────────────────────────────────
    schema_version = None
    schema_ok = False
    db_reachable = False
    try:
        result = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        row = result.first()
        db_reachable = True
        if row:
            schema_version = row[0]
            schema_ok = schema_version == EXPECTED_SCHEMA_VERSION
    except Exception:
        pass

    # ── LLM ──────────────────────────────────────────────────────────────
    resolved = await mc_svc.resolve_active_model_config(db)
    configs = await mc_svc.list_model_configs(db)

    # ── VLM ──────────────────────────────────────────────────────────────
    vlm_resolved = await mc_svc.resolve_active_vlm_config(db)
    vlm_section: dict = {"available": False}
    if vlm_resolved:
        vlm_section = {
            "available": True,
            "source": vlm_resolved.source,
            "name": vlm_resolved.name,
            "model_id": vlm_resolved.model_id,
            "base_url": vlm_resolved.base_url,
            "api_key_masked": _mask_api_key(vlm_resolved.api_key),
            "supports_vision": vlm_resolved.supports_vision,
            "supports_http_image_url": vlm_resolved.supports_http_image_url,
            "supports_data_uri_image": vlm_resolved.supports_data_uri_image,
        }

    llm_configs = [c for c in configs if getattr(c, "model_type", "llm") == "llm"]
    vlm_configs = [c for c in configs if getattr(c, "model_type", "llm") == "vlm"]

    return ok({
        "instance": {
            "instance_id": _INSTANCE_ID,
            "pid": _os.getpid(),
            "started_at": _STARTED_AT,
            "version": settings.app_version,
        },
        "schema": {
            "version": schema_version,
            "expected": EXPECTED_SCHEMA_VERSION,
            "ok": schema_ok,
            "db_reachable": db_reachable,
        },
        "model": {
            "source": resolved.source,
            "name": resolved.name,
            "model_id": resolved.model_id,
            "base_url": resolved.base_url,
            "api_key_masked": _mask_api_key(resolved.api_key),
            "context_window": resolved.context_window,
        },
        "vlm": vlm_section,
        "config_summary": {
            "total_configs": len(configs),
            "llm_configs": len(llm_configs),
            "vlm_configs": len(vlm_configs),
            "enabled_configs": sum(1 for c in configs if c.is_enabled),
            "default_llm": next(
                (c.name for c in llm_configs if c.is_default), None
            ),
            "default_vlm": next(
                (c.name for c in vlm_configs if c.is_default), None
            ),
        },
        "env_fallback": {
            "local_llm_url": settings.local_llm_url,
            "default_model_id": settings.default_model_id,
            "local_vlm_url": settings.local_vlm_url,
            "default_vlm_id": settings.default_vlm_id,
            "workspace_root": settings.workspace_root,
            "db_pool_size": settings.db_pool_size,
            "db_max_overflow": settings.db_max_overflow,
            "debug": settings.debug,
        },
        "tools": _get_tools_metadata(),
    })


def _get_tools_metadata() -> dict:
    """Collect metadata from all registered tools for diagnostics."""
    from tools.registry import get_registry
    registry = get_registry()
    return {
        "count": len(registry.tools),
        "metadata": registry.list_tool_metadata(),
    }
