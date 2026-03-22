"""Service layer for user_skills relationship table (Phase H1).

Handles install/uninstall sync, usage tracking, enable/disable,
and queries for user profile and admin views.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from skills.models import UserSkill


async def upsert_user_skill(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
    version: str,
) -> UserSkill:
    """Insert or update a user_skill record on install.

    If the user already has this skill (by name), update version + reset enabled.
    """
    existing = await get_user_skill(db, user_id, skill_name)
    now = datetime.now(timezone.utc)
    if existing:
        existing.version = version
        existing.is_enabled = True
        existing.updated_at = now
        await db.flush()
        return existing

    us = UserSkill(
        id=uuid.uuid4(),
        user_id=user_id,
        skill_name=skill_name,
        version=version,
        is_enabled=True,
        usage_count=0,
        installed_at=now,
        updated_at=now,
    )
    db.add(us)
    await db.flush()
    return us


async def remove_user_skill(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
) -> bool:
    """Delete user_skill record on uninstall. Returns True if existed."""
    from sqlalchemy import delete
    result = await db.execute(
        delete(UserSkill).where(
            and_(UserSkill.user_id == user_id, UserSkill.skill_name == skill_name)
        )
    )
    return result.rowcount > 0


async def get_user_skill(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
) -> Optional[UserSkill]:
    """Get a single user_skill record."""
    result = await db.execute(
        select(UserSkill).where(
            and_(UserSkill.user_id == user_id, UserSkill.skill_name == skill_name)
        )
    )
    return result.scalar_one_or_none()


async def list_user_skills(
    db: AsyncSession,
    user_id: uuid.UUID,
    enabled_only: bool = False,
) -> list[UserSkill]:
    """Return all user_skills for a user, ordered by usage_count DESC, name ASC."""
    q = select(UserSkill).where(UserSkill.user_id == user_id)
    if enabled_only:
        q = q.where(UserSkill.is_enabled == True)  # noqa: E712
    q = q.order_by(UserSkill.usage_count.desc(), UserSkill.skill_name.asc())
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


async def set_enabled(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
    is_enabled: bool,
) -> Optional[UserSkill]:
    """Enable or disable a user's skill. Returns updated record or None."""
    us = await get_user_skill(db, user_id, skill_name)
    if not us:
        return None
    us.is_enabled = is_enabled
    us.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return us


async def increment_usage(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
) -> None:
    """Increment usage_count and set last_used_at for a user_skill."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(UserSkill)
        .where(
            and_(UserSkill.user_id == user_id, UserSkill.skill_name == skill_name)
        )
        .values(
            usage_count=UserSkill.usage_count + 1,
            last_used_at=now,
            updated_at=now,
        )
    )


async def is_skill_enabled_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    skill_name: str,
) -> bool:
    """Check if a skill is enabled for a user.

    Returns True if the skill is installed and enabled, or if it's not
    installed at all (not-installed means no admin block).
    """
    us = await get_user_skill(db, user_id, skill_name)
    if us is None:
        return True  # No record = not blocked by admin
    return us.is_enabled
