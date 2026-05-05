"""Core extension metadata API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_current_user
from auth.models import User
from core.response import ok
from extensions.registry import get_extension_registry


router = APIRouter()


@router.get("")
async def list_extensions(current_user: User = Depends(get_current_user)):
    registry = get_extension_registry()
    role = getattr(current_user, "role", None)
    return ok({"extensions": registry.metadata_for_role(role)})


@router.get("/{name}")
async def get_extension(name: str, current_user: User = Depends(get_current_user)):
    registry = get_extension_registry()
    runtime = registry.get_runtime(name)
    role = getattr(current_user, "role", None)
    if runtime is None or runtime.manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Extension not found"},
        )
    visible = runtime in registry.get_enabled_extensions(role)
    if not visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Extension not found"},
        )
    return ok(runtime.manifest.public_metadata(
        include_error=role == "admin",
        status=runtime.status,
        error=runtime.error,
    ))

