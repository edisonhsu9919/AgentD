"""Admin user management router (Phase C.5, enhanced Phase H2).

Provides admin-only endpoints for creating, listing, viewing, and modifying
users, plus user skills management and session monitoring.

All endpoints require the ``require_admin`` dependency.
This is NOT a self-service registration API — only admins can create users.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_admin
from auth import service as auth_svc
from auth.models import User
from auth.schemas import (
    CreateUserRequest,
    UpdateUserRequest,
    UserProfileResponse,
    UserResponse,
    UserSkillItem,
    UserSkillToggleRequest,
)
from core.database import get_db
from core.response import ok, ok_list
from workspace.manager import create_workspace, ensure_user_root

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# User CRUD (Phase C.5)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("")
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Create a new user with workspace initialization."""
    existing = await auth_svc.get_user_by_username(db, body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": "Username already taken"},
        )

    user = await auth_svc.create_user(
        db,
        username=body.username,
        password=body.password,
        role=body.role,
        workspace="",
        department=body.department,
        employee_id=body.employee_id,
    )

    user.workspace = create_workspace(str(user.id))
    ensure_user_root(user.workspace)

    if not body.is_active:
        user.is_active = False

    await db.commit()
    return ok(UserResponse.model_validate(user).model_dump(mode="json"))


@router.get("")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List all users with pagination."""
    users, total = await auth_svc.list_users(db, page=page, page_size=page_size)
    # Enrich with installed_skill_count
    from skills import user_skill_service as us_svc
    data = []
    for u in users:
        d = UserResponse.model_validate(u).model_dump(mode="json")
        skills = await us_svc.list_user_skills(db, u.id)
        d["installed_skill_count"] = len(skills)
        data.append(d)
    return ok_list(data, total=total, page=page, page_size=page_size)


@router.get("/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get a single user's full profile (same shape as /api/auth/me/profile)."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from skills import user_skill_service as us_svc
    from skills import service as skill_svc

    user_skills = await us_svc.list_user_skills(db, user.id)
    skill_items: list[dict] = []
    for us in user_skills:
        icon = ""
        catalog_skill = await skill_svc.get_skill_by_name_version(
            db, us.skill_name, us.version,
        )
        if catalog_skill:
            icon = catalog_skill.icon
        skill_items.append(
            UserSkillItem(
                name=us.skill_name,
                version=us.version,
                is_enabled=us.is_enabled,
                usage_count=us.usage_count,
                last_used_at=us.last_used_at,
                icon=icon,
            ).model_dump(mode="json")
        )

    profile = UserProfileResponse.model_validate(user).model_dump(mode="json")
    profile["installed_skills"] = skill_items
    return ok(profile)


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update a user's role, active status, password, or profile fields."""
    if body.is_active is False and user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BAD_REQUEST", "message": "Cannot deactivate your own account"},
        )

    user = await auth_svc.update_user(
        db,
        user_id=user_id,
        role=body.role,
        is_active=body.is_active,
        password=body.password,
        department=body.department,
        employee_id=body.employee_id,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    await db.commit()
    return ok(UserResponse.model_validate(user).model_dump(mode="json"))


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Admin user skills management
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/{user_id}/skills")
async def get_user_skills(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List all installed skills for a user with usage stats."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from skills import user_skill_service as us_svc
    from skills import service as skill_svc

    user_skills = await us_svc.list_user_skills(db, user.id)
    items: list[dict] = []
    for us in user_skills:
        icon = ""
        catalog_skill = await skill_svc.get_skill_by_name_version(
            db, us.skill_name, us.version,
        )
        if catalog_skill:
            icon = catalog_skill.icon
        items.append(
            UserSkillItem(
                name=us.skill_name,
                version=us.version,
                is_enabled=us.is_enabled,
                usage_count=us.usage_count,
                last_used_at=us.last_used_at,
                icon=icon,
            ).model_dump(mode="json")
        )

    return ok_list(items, total=len(items))


@router.patch("/{user_id}/skills/{skill_name}")
async def toggle_user_skill(
    user_id: uuid.UUID,
    skill_name: str,
    body: UserSkillToggleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Enable or disable a specific skill for a user."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from skills import user_skill_service as us_svc

    updated = await us_svc.set_enabled(db, user.id, skill_name, body.is_enabled)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": f"Skill '{skill_name}' not installed for this user"},
        )

    return ok({
        "skill_name": updated.skill_name,
        "is_enabled": updated.is_enabled,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# H2: Admin session monitoring (read-only)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/{user_id}/sessions")
async def get_user_sessions(
    user_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List sessions for a specific user (admin read-only)."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from session import service as session_svc
    from session.schemas import SessionResponse

    sessions, total = await session_svc.list_sessions(
        db, user.id, page=page, page_size=page_size,
    )
    data = [SessionResponse.model_validate(s).model_dump(mode="json") for s in sessions]
    return ok_list(data, total=total, page=page, page_size=page_size)


@router.get("/{user_id}/sessions/{session_id}")
async def get_user_session(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get a specific session detail (admin read-only)."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from session import service as session_svc
    from session.schemas import SessionResponse

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found for this user"},
        )

    return ok(SessionResponse.model_validate(session).model_dump(mode="json"))


@router.get("/{user_id}/sessions/{session_id}/messages")
async def get_user_session_messages(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get messages for a user's session (admin read-only)."""
    user = await auth_svc.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "User not found"},
        )

    from session import service as session_svc
    from session.schemas import MessageResponse

    session = await session_svc.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Session not found for this user"},
        )

    messages = await session_svc.list_messages(db, session_id)
    data = [MessageResponse.model_validate(m).model_dump(mode="json") for m in messages]
    return ok_list(data, total=len(data))
