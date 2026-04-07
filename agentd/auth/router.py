import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from auth import service as auth_svc
from auth.models import User
from auth.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserProfileResponse,
    UserResponse,
    UserSkillItem,
)
from core.config import settings
from core.database import get_db
from core.response import ok

router = APIRouter()


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await auth_svc.authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"},
        )
    # Self-heal user directory on login (Phase 6.7)
    from workspace.manager import ensure_user_root
    ensure_user_root(user.workspace)

    return ok(
        TokenResponse(
            access_token=auth_svc.create_access_token(user),
            refresh_token=auth_svc.create_refresh_token(user),
            expires_in=settings.access_token_expire_minutes * 60,
            user=UserResponse.model_validate(user),
        ).model_dump()
    )


@router.post("/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = auth_svc.verify_token(body.refresh_token, token_type="refresh")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or expired refresh token"},
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token payload"},
        )
    user = await auth_svc.get_user_by_id(db, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "User not found or inactive"},
        )
    return ok(
        AccessTokenResponse(
            access_token=auth_svc.create_access_token(user),
            expires_in=settings.access_token_expire_minutes * 60,
        ).model_dump()
    )


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return ok(UserResponse.model_validate(current_user).model_dump())


@router.get("/me/profile")
async def me_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return full user profile with installed skills.

    Phase 6: installed skills truth comes from the filesystem.
    user_skills table only enriches with usage/enabled metadata.
    Orphan user_skills records (no local directory) are cleaned up lazily.
    """
    from skills import user_skill_service as us_svc
    from skills import service as skill_svc
    from skills.filesystem import list_installed_skills_fs

    # Step 1: filesystem truth
    fs_skills = list_installed_skills_fs(current_user.workspace)
    fs_names = {s["name"] for s in fs_skills}

    # Step 2: user_skills metadata (usage, enabled, etc.)
    user_skills = await us_svc.list_user_skills(db, current_user.id)
    us_map = {us.skill_name: us for us in user_skills}

    # Step 3: build response — only include skills that exist on filesystem
    skill_items: list[dict] = []
    for fs in fs_skills:
        name = fs["name"]
        us = us_map.get(name)
        icon = fs.get("icon", "")
        if not icon:
            catalog_skill = await skill_svc.get_skill_by_name_version(
                db, name, fs.get("version", ""),
            )
            if catalog_skill:
                icon = catalog_skill.icon

        skill_items.append(
            UserSkillItem(
                name=name,
                version=fs.get("version", "0.1.0"),
                is_enabled=us.is_enabled if us else True,
                usage_count=us.usage_count if us else 0,
                last_used_at=us.last_used_at if us else None,
                icon=icon,
            ).model_dump(mode="json")
        )

    # Step 4: lazy orphan cleanup — remove user_skills records for missing skills
    orphan_names = set(us_map.keys()) - fs_names
    if orphan_names:
        for orphan_name in orphan_names:
            await us_svc.remove_user_skill(db, current_user.id, orphan_name)
        await db.commit()

    profile = UserProfileResponse.model_validate(current_user).model_dump(mode="json")
    profile["installed_skills"] = skill_items
    return ok(profile)
